import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as T
import numpy as np
import sys
from typing import List, Optional
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
        for h in logger.handlers: h.flush()
        
        # Helper to load backbone with timeout to prevent hang
        def _load_backbone_safe(name, weights_enum, use_weights: bool):
            import threading
            result_container = {}
            
            def _load():
                try:
                    logger.info(f"Attempting to load {name} (weights={use_weights})...")
                    for h in logger.handlers: h.flush()
                    if name == "resnet50":
                        w = weights_enum.DEFAULT if use_weights else None
                        result_container['model'] = models.resnet50(weights=w)
                    elif name == "mobilenet_v3_small":
                        w = weights_enum.DEFAULT if use_weights else None
                        result_container['model'] = models.mobilenet_v3_small(weights=w)
                    logger.info(f"Thread: Loaded {name} successfully.")
                    for h in logger.handlers: h.flush()
                except Exception as e:
                    logger.error(f"Thread: Error loading {name}: {e}")
                    result_container['error'] = e

            # Start loading in a thread
            t = threading.Thread(target=_load, daemon=True)
            t.start()
            t.join(timeout=30) # 30s timeout for download/load
            
            if t.is_alive():
                logger.error(f"Timeout loading {name} weights! Network might be blocked. Fallback to random weights.")
                # We can't kill the thread, but we can return a fresh model with random weights
                if name == "resnet50":
                    return models.resnet50(weights=None)
                elif name == "mobilenet_v3_small":
                    return models.mobilenet_v3_small(weights=None)
            
            if 'error' in result_container:
                logger.error(f"Error loading {name}: {result_container['error']}. Fallback to random weights.")
                if name == "resnet50":
                    return models.resnet50(weights=None)
                elif name == "mobilenet_v3_small":
                    return models.mobilenet_v3_small(weights=None)
            
            if 'model' in result_container:
                logger.info(f"Successfully loaded {name}.")
                for h in logger.handlers: h.flush()
                return result_container['model']
            
            return None

        # Load backbone
        use_pretrained = not self.reid_cfg.get("model_path")
        
        if self.backbone_name == "resnet50":
            self.backbone = _load_backbone_safe("resnet50", models.ResNet50_Weights, use_pretrained)
            if self.backbone is None: # Should not happen with fallback
                 self.backbone = models.resnet50(weights=None)
            
            num_features = self.backbone.fc.in_features
            self.backbone.fc = nn.Sequential(
                nn.Linear(num_features, self.embedding_dim),
                nn.BatchNorm1d(self.embedding_dim)
            )
        elif self.backbone_name == "mobilenet_v3_small":
            self.backbone = _load_backbone_safe("mobilenet_v3_small", models.MobileNet_V3_Small_Weights, use_pretrained)
            if self.backbone is None:
                 self.backbone = models.mobilenet_v3_small(weights=None)

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
                    logger.info(f"Loading custom weights from {full_weights_path}...")
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
        
        logger.info(f"Moving backbone to device: {self.device}")
        for h in logger.handlers: h.flush()
        self.backbone.to(self.device)
        
        logger.info("Setting backbone to eval mode")
        for h in logger.handlers: h.flush()
        self.backbone.eval()
        
        # Standard ImageNet normalization for pre-trained models
        logger.info("Creating transforms")
        for h in logger.handlers: h.flush()
        self.transform = T.Compose([
            T.ToPILImage(),
            T.Resize(self.input_size),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        logger.info("ReID Embedder initialization complete.")
        for h in logger.handlers: h.flush()

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

    @torch.no_grad()
    def get_batch_embeddings(self, images: List[np.ndarray]) -> List[Optional[np.ndarray]]:
        """
        Generates normalized embedding vectors for a batch of cropped vehicle images.
        """
        if not images:
            return []

        valid_images = []
        indices = []
        embeddings_map = {}

        # Pre-filter invalid images
        for idx, img in enumerate(images):
            if img is not None and img.size > 0:
                valid_images.append(img)
                indices.append(idx)
            else:
                embeddings_map[idx] = None

        if not valid_images:
            return [None] * len(images)

        try:
            # Batch transform
            batch_tensors = []
            for img in valid_images:
                batch_tensors.append(self.transform(img))
            
            input_tensor = torch.stack(batch_tensors).to(self.device)

            # Forward pass
            embeddings = self.backbone(input_tensor)

            # L2 Normalize
            embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
            embeddings_np = embeddings.cpu().numpy()

            # Map back to original indices
            for i, idx in enumerate(indices):
                embeddings_map[idx] = embeddings_np[i]

            # Construct result list in order
            return [embeddings_map.get(i) for i in range(len(images))]

        except Exception as e:
            logger.error(f"ReID batch embedding failed: {e}")
            return [None] * len(images)

    def compute_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """
        Computes cosine similarity between two embeddings.
        Since they are L2 normalized, this is just the dot product.
        """
        return np.dot(emb1, emb2)
