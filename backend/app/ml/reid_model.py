import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as T
import numpy as np
import cv2
from typing import List, Optional, Union
import logging

logger = logging.getLogger("app.ml.reid")

class ReIDEmbedder:
    def __init__(self, config: dict):
        self.config = config
        self.device = "cuda" if config.get("performance", {}).get("gpu_acceleration", False) and torch.cuda.is_available() else "cpu"
        self.embedding_dim = 128
        
        logger.info(f"Initializing ReID Embedder on {self.device}...")
        
        # Use MobileNetV3 Small as a fast, lightweight backbone
        self.backbone = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
        # Replace the classifier with a simple linear layer to get the desired embedding size
        num_features = self.backbone.classifier[0].in_features
        self.backbone.classifier = nn.Sequential(
            nn.Linear(num_features, self.embedding_dim),
            nn.BatchNorm1d(self.embedding_dim)
        )
        
        self.backbone.to(self.device)
        self.backbone.eval()
        
        # Standard ImageNet normalization for pre-trained models
        self.transform = T.Compose([
            T.ToPILImage(),
            T.Resize((128, 128)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    @torch.no_grad()
    def get_embedding(self, image: np.ndarray) -> Optional[np.ndarray]:
        """
        Generates a normalized embedding vector for a cropped vehicle image.
        """
        if image.size == 0:
            return None
            
        try:
            # Prepare image (CoreModule provides RGB)
            input_tensor = self.transform(image).unsqueeze(0).to(self.device)
            
            # Forward pass
            embedding = self.backbone(input_tensor)
            
            # L2 Normalize
            embedding = torch.nn.functional.normalize(embedding, p=2, dim=1)
            
            return embedding.cpu().numpy()[0]
        except Exception as e:
            logger.error(f"ReID embedding failed: {e}")
            return None

    def compute_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """
        Computes cosine similarity between two embeddings.
        Since they are L2 normalized, this is just the dot product.
        """
        return np.dot(emb1, emb2)
