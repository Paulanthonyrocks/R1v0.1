import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as T
import numpy as np
from typing import List, Optional
import logging

logger = logging.getLogger("app.ml.reid")

class ReIDEmbedder:
    def __init__(self, config: dict, device: Optional[str] = None):
        """
        Args:
            config: Application configuration dict.
            device: Optional device override (e.g. 'cuda:1'). When provided, it
                    takes precedence over the config-level gpu_acceleration flag.
                    This allows per-worker GPU assignment in multi-GPU setups.
        """
        self.config = config
        self.reid_cfg = config.get("vehicle_detection", {}).get("reid", {})

        if device is not None:
            self.device = device
        elif config.get("performance", {}).get("gpu_acceleration", False) and torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"
        
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
        
        # Fast cv2 preprocessing (Sep-10): the torchvision T.Compose path ran
        # ToPILImage -> Resize(256) -> ToTensor per crop -- PIL resize in
        # Python plus a per-crop tensor build. TRACK-SUBSTAGES measured the
        # assoc+update embed path at 103-123 ms/frame (56-79% of all remaining
        # track_post cost). cv2.resize + one stacked torch.from_numpy is
        # numerically equivalent (bilinear + ImageNet normalize) and 10-30x
        # cheaper. The PIL path is retained below as a fallback for exotic
        # dtypes/strides cv2 can't take.
        # Standard ImageNet normalization for pre-trained models
        self.transform = T.Compose([
            T.ToPILImage(),
            T.Resize(self.input_size),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        self._mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        self._std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)

    def _preprocess_batch(self, images: List[np.ndarray]):
        """Fast cv2 path for a list of uint8 HWC crops.

        Resize each crop (bilinear, same as T.Resize), stack ONCE in numpy
        (uint8, single allocation), zero-copy into a torch tensor. The caller
        moves it to the device AS UINT8 (4x smaller H2D than float32) and does
        the float/255 + ImageNet normalize ON THE GPU -- the CPU-side float
        conversion + per-crop tensor builds were the actual cost of the old
        PIL path and of a naive cv2 port that still converted on CPU.

        Returns (N, H, W, 3) uint8 tensor, or None when ANY crop is not a
        uint8 ndarray (caller falls back to the PIL compose for the batch).
        """
        try:
            import cv2 as _cv
            h, w = self.input_size[1], self.input_size[0]
            resized = []
            for img in images:
                if not isinstance(img, np.ndarray) or img.dtype != np.uint8:
                    return None
                resized.append(_cv.resize(img, (w, h), interpolation=_cv.INTER_LINEAR))
            arr = np.stack(resized)  # (N, H, W, 3) uint8
            return torch.from_numpy(arr)
        except Exception:
            return None

    def _normalize_batch(self, batch: torch.Tensor) -> torch.Tensor:
        """ImageNet normalization, batched (equivalent to T.Normalize)."""
        mean = self._mean.to(batch.device)
        std = self._std.to(batch.device)
        return (batch - mean) / std

    @torch.no_grad()
    def get_embedding(self, image: np.ndarray) -> Optional[np.ndarray]:
        """
        Generates a normalized embedding vector for a cropped vehicle image.
        """
        if image.size == 0:
            return None
            
        try:
            # Prepare image (CoreModule provides RGB) -- same fast path as the
            # batch embedder; PIL fallback for non-uint8 crops.
            batched = self._preprocess_batch([image])
            if batched is not None:
                x = batched.to(self.device)
                x = x.permute(0, 3, 1, 2).contiguous().float().div_(255.0)
                input_tensor = self._normalize_batch(x)
            else:
                input_tensor = self.transform(image).unsqueeze(0).to(self.device)
                input_tensor = self._normalize_batch(input_tensor)

            # Forward pass
            if self.device.startswith("cuda"):
                with torch.autocast(self.device, dtype=torch.float16):
                    embedding = self.backbone(input_tensor)
            else:
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
            # Fast path (Sep-10): resize+stack as uint8 numpy (one allocation),
            # H2D as uint8 (4x smaller transfer), float/255 + ImageNet normalize
            # ON THE GPU. The old per-crop PIL compose was the dominant ReID
            # cost (TRACK-SUBSTAGES: reid_assoc 103-123 ms/frame). Falls back
            # to the PIL compose when any crop is not a uint8 ndarray.
            batched = self._preprocess_batch(valid_images)
            if batched is not None:
                x = batched.to(self.device)                       # (N,H,W,3) uint8
                x = x.permute(0, 3, 1, 2).contiguous().float().div_(255.0)
                input_tensor = self._normalize_batch(x)
            else:
                # Any non-uint8 crop -> PIL compose per crop (slow path, rare).
                input_tensor = torch.stack(
                    [self.transform(img) for img in valid_images]
                ).to(self.device)

            # Forward pass (fp16 when CUDA)
            if self.device.startswith("cuda"):
                with torch.autocast(self.device, dtype=torch.float16):
                    embeddings = self.backbone(input_tensor)
            else:
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
