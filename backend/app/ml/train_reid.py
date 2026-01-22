import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset
import torchvision.transforms as T
import torchvision.models as models
import numpy as np
import logging
import os
from pathlib import Path
from typing import List, Tuple, Optional
import cv2
from PIL import Image

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reid_train")

class ReIDDataset(Dataset):
    """
    Generic ReID Dataset. Expects a directory structure where each subdirectory 
    is a vehicle ID containing images of that vehicle.
    """
    def __init__(self, root_dir: str, transform=None):
        self.root_dir = Path(root_dir)
        self.transform = transform
        self.image_paths = []
        self.labels = []
        
        if not self.root_dir.exists():
            logger.error(f"Dataset root {root_dir} does not exist.")
            return

        # Find all identity folders
        identities = sorted([d for d in self.root_dir.iterdir() if d.is_dir()])
        self.id_to_label = {id_path.name: i for i, id_path in enumerate(identities)}
        
        for id_path in identities:
            label = self.id_to_label[id_path.name]
            for img_path in id_path.glob("*.jpg"):
                self.image_paths.append(img_path)
                self.labels.append(label)
            for img_path in id_path.glob("*.png"):
                self.image_paths.append(img_path)
                self.labels.append(label)
                
        logger.info(f"Loaded {len(self.image_paths)} images from {len(identities)} identities.")

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        label = self.labels[idx]
        
        img = Image.open(img_path).convert('RGB')
        
        if self.transform:
            img = self.transform(img)
            
        return img, label

class TripletLoss(nn.Module):
    """
    Standard Triplet Loss for metric learning.
    """
    def __init__(self, margin=0.3):
        super(TripletLoss, self).__init__()
        self.margin = margin
        self.ranking_loss = nn.MarginRankingLoss(margin=margin)

    def forward(self, inputs, targets):
        n = inputs.size(0)
        # Compute pairwise distance matrix
        dist = torch.pow(inputs, 2).sum(dim=1, keepdim=True).expand(n, n)
        dist = dist + dist.t()
        dist.addmm_(1, -2, inputs, inputs.t())
        dist = dist.clamp(min=1e-12).sqrt()  # for numerical stability

        # For each anchor, find the hardest positive and hardest negative
        mask = targets.expand(n, n).eq(targets.expand(n, n).t())
        dist_ap, dist_an = [], []
        for i in range(n):
            dist_ap.append(dist[i][mask[i]].max().unsqueeze(0))
            dist_an.append(dist[i][mask[i] == 0].min().unsqueeze(0))
        dist_ap = torch.cat(dist_ap)
        dist_an = torch.cat(dist_an)

        # Compute ranking hinge loss
        y = torch.ones_like(dist_an)
        loss = self.ranking_loss(dist_an, dist_ap, y)
        return loss

class ReIDModel(nn.Module):
    def __init__(self, backbone_name="resnet50", embedding_dim=128, num_classes=None):
        super(ReIDModel, self).__init__()
        
        if backbone_name == "resnet50":
            self.backbone = models.resnet50(weights=models.ResNet50_Weights.DEFAULT)
            num_features = self.backbone.fc.in_features
            # Remove FC layer
            self.backbone.fc = nn.Identity()
        elif backbone_name == "mobilenet_v3_small":
            self.backbone = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
            num_features = self.backbone.classifier[0].in_features
            self.backbone.classifier = nn.Identity()
        else:
            raise ValueError(f"Unsupported backbone: {backbone_name}")

        self.embedding_head = nn.Sequential(
            nn.Linear(num_features, embedding_dim),
            nn.BatchNorm1d(embedding_dim)
        )
        
        self.num_classes = num_classes
        if num_classes:
            self.classifier = nn.Linear(embedding_dim, num_classes)

    def forward(self, x):
        features = self.backbone(x)
        # If backbone doesn't include global pooling, add it here
        if len(features.shape) > 2:
            features = torch.nn.functional.adaptive_avg_pool2d(features, (1, 1))
            features = torch.flatten(features, 1)
            
        embedding = self.embedding_head(features)
        
        if self.training and self.num_classes:
            logits = self.classifier(embedding)
            return embedding, logits
        
        return embedding

def train_reid(
    data_dir: str,
    output_path: str,
    backbone: str = "resnet50",
    epochs: int = 50,
    batch_size: int = 32,
    lr: float = 0.001,
    resolution: int = 256
):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    # 1. Data Augmentation
    train_transform = T.Compose([
        T.Resize((resolution, resolution)),
        T.RandomHorizontalFlip(),
        T.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.1),
        T.RandomAffine(degrees=10, translate=(0.1, 0.1), scale=(0.9, 1.1)),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    
    dataset = ReIDDataset(data_dir, transform=train_transform)
    if len(dataset) == 0:
        logger.error("No data found for training.")
        return
        
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=4)
    
    # 2. Model
    model = ReIDModel(backbone_name=backbone, embedding_dim=128, num_classes=len(dataset.id_to_label))
    model.to(device)
    
    # 3. Loss Functions
    criterion_id = nn.CrossEntropyLoss()
    criterion_triplet = TripletLoss(margin=0.3)
    
    optimizer = optim.Adam(model.parameters(), lr=lr)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.1)
    
    logger.info(f"Starting training on {device}...")
    
    for epoch in range(epochs):
        model.train()
        running_loss = 0.0
        
        for images, labels in dataloader:
            images, labels = images.to(device), labels.to(device)
            
            optimizer.zero_grad()
            
            embeddings, logits = model(images)
            
            # Combine losses
            loss_id = criterion_id(logits, labels)
            
            # For triplet loss, we usually need multiple instances per ID in a batch
            # If the batch doesn't have enough, it might be less effective
            loss_triplet = criterion_triplet(embeddings, labels)
            
            loss = loss_id + loss_triplet
            
            loss.backward()
            optimizer.step()
            
            running_loss += loss.item()
            
        scheduler.step()
        logger.info(f"Epoch {epoch+1}/{epochs}, Loss: {running_loss/len(dataloader):.4f}")
        
    # Save the trained model
    torch.save(model.state_dict(), output_path)
    logger.info(f"Model saved to {output_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", type=str, required=True, help="Path to identity-organized images")
    parser.add_argument("--output", type=str, default="backend/models/reid_resnet50.pth")
    parser.add_argument("--backbone", type=str, default="resnet50")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch_size", type=int, default=32)
    parser.add_argument("--res", type=int, default=256)
    args = parser.parse_args()
    
    train_reid(args.data_dir, args.output, args.backbone, args.epochs, args.batch_size, resolution=args.res)
