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
        self.reid_cfg = config.get("vehicle_detection", {}).get("reid", {})
        self.device = "cuda" if config.get("performance", {}).get("gpu_acceleration", False) and torch.cuda.is_available() else "cpu"
        
        # Configuration options
        self.backbone_name = self.reid_cfg.get("backbone", "mobilenet_v3_small")
        self.input_size = self.reid_cfg.get("input_size", (256, 256)) # Default to 256x256 as suggested
        self.embedding_dim = self.reid_cfg.get("embedding_dim", 128)
        
        logger.info(f"Initializing ReID Embedder ({self.backbone_name}) on {self.device}...")
        
        # Load backbone
        if self.backbone_name == "resnet50":
            self.backbone = models.resnet50(weights=models.ResNet50_Weights.DEFAULT if not self.reid_cfg.get("model_path") else None)
            num_features = self.backbone.fc.in_features
            self.backbone.fc = nn.Sequential(
                nn.Linear(num_features, self.embedding_dim),
                nn.BatchNorm1d(self.embedding_dim)
            )
        elif self.backbone_name == "mobilenet_v3_small":
            self.backbone = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT if not self.reid_cfg.get("model_path") else None)
            num_features = self.backbone.classifier[0].in_features
            self.backbone.classifier = nn.Sequential(
                nn.Linear(num_features, self.embedding_dim),
                nn.BatchNorm1d(self.embedding_dim)
            )
        else:
            logger.warning(f"Unknown backbone {self.backbone_name}, falling back to mobilenet_v3_small")
            self.backbone = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
            num_features = self.backbone.classifier[0].in_features
            self.backbone.classifier = nn.Sequential(
                nn.Linear(num_features, self.embedding_dim),
                nn.BatchNorm1d(self.embedding_dim)
            )
            
        # Load custom weights if provided
        weights_path = self.reid_cfg.get("model_path")
        if weights_path:
            try:
                project_root = Path(config.get("project_root_dir", ""))
                full_weights_path = project_root / weights_path
                if full_weights_path.exists():
                    state_dict = torch.load(full_weights_path, map_location=self.device)
                    # Filter out 'classifier.' or 'embedding_head.' prefixes if they come from train_reid.py
                    # and map them to our backbone structure
                    new_state_dict = {}
                    for k, v in state_dict.items():
                        if k.startswith("backbone."):
                            new_state_dict[k[9:]] = v
                        elif k.startswith("embedding_head."):
                            if self.backbone_name == "resnet50":
                                new_state_dict["fc." + k[15:]] = v
                            else:
                                new_state_dict["classifier." + k[15:]] = v
                    
                    self.backbone.load_state_dict(new_state_dict, strict=False)
                    logger.info(f"Loaded ReID weights from {full_weights_path}")
                else:
                    logger.warning(f"ReID weights file not found at {full_weights_path}")
            except Exception as e:
                logger.error(f"Failed to load ReID weights: {e}")
        
        self.backbone.to(self.device)
        self.backbone.eval()
        
        # Standard ImageNet normalization for pre-trained models
        self.transform = T.Compose([
            T.ToPILImage(),
            T.Resize(self.input_size),
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
