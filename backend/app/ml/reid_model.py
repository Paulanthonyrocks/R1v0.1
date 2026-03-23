import openvino.runtime as ov
import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as T
import numpy as np
import sys
import os
from pathlib import Path
from typing import List, Optional
import logging
import onnxruntime as ort

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
        self.use_onnx = self.reid_cfg.get("use_onnx", True)
        self.onnx_session = None
        
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
                project_root = Path(self.config.get("project_root_dir", ""))
                full_weights_path = project_root / weights_path
                
                # --- NEW: Force ignore XML files before PyTorch crashes ---
                if str(full_weights_path).endswith(".xml"):
                    logger.warning(f"Bypassing OpenVINO .xml file for PyTorch backbone: {full_weights_path}")
                elif full_weights_path.exists():
                    logger.info(f"Loading custom weights from {full_weights_path}...")
                    # Use weights_only=False for custom legacy weights
                    state_dict = torch.load(full_weights_path, map_location=self.device, weights_only=False)
                    
                    # Filter out 'backbone.' or 'embedding_head.' prefixes
                    new_state_dict = {}
                    for k, v in state_dict.items():
                        if k.startswith("backbone."):
                            new_state_dict[k[9:]] = v
                        elif k.startswith("embedding_head."):
                            if self.backbone_name == "resnet50":
                                new_state_dict["fc." + k[15:]] = v
                            else:
                                new_state_dict["classifier." + k[15:]] = v
                        else:
                            new_state_dict[k] = v
                    
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
        
        if self.use_onnx:
            self._initialize_onnx()
        
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

    def _initialize_onnx(self):
        project_root = Path(self.config.get("project_root_dir", ""))
        models_dir = project_root / "backend/models"
        models_dir.mkdir(parents=True, exist_ok=True)
        onnx_path = models_dir / f"reid_{self.backbone_name}.onnx"

        # 1. Export the model if it doesn't exist
        if not onnx_path.exists():
            logger.info(f"ONNX model not found at {onnx_path}. Exporting PyTorch model...")
            try:
                dummy_input = torch.randn(1, 3, self.input_size[0], self.input_size[1]).to(self.device)
                torch.onnx.export(
                    self.backbone, 
                    dummy_input, 
                    str(onnx_path), 
                    export_params=True,
                    opset_version=12,
                    do_constant_folding=True,
                    input_names=['input'],
                    output_names=['output'],
                    dynamic_axes={'input': {0: 'batch_size'}, 'output': {0: 'batch_size'}}
                )
                logger.info("ONNX export successful.")
            except Exception as e:
                logger.error(f"Failed to export ONNX model: {e}")
                self.onnx_session = None
                return

        # 2. Load the FP32 ONNX model into InferenceSession
        try:
            logger.info("Loading FP32 ONNX model...")
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if self.device == "cuda" else ['CPUExecutionProvider']
            self.onnx_session = ort.InferenceSession(str(onnx_path), providers=providers)
            logger.info(f"ONNX (FP32) InferenceSession loaded successfully with providers: {self.onnx_session.get_providers()}")
        except Exception as e:
            logger.error(f"Failed to load ONNX session: {e}")
            self.onnx_session = None
    def _center_crop_numpy(self, img: np.ndarray, crop_ratio: float = 0.7) -> np.ndarray:
        h, w = img.shape[:2]
        ch, cw = int(h * crop_ratio), int(w * crop_ratio)
        y1, x1 = (h - ch) // 2, (w - cw) // 2
        return img[y1:y1+ch, x1:x1+cw]

    @torch.no_grad()
    def get_embedding(self, image: np.ndarray) -> Optional[np.ndarray]:
        """
        Generates a normalized multi-scale embedding vector (full + center crop).
        """
        if image.size == 0:
            return None
            
        try:
            # Scale 1: Full image
            input_tensor_full = self.transform(image).unsqueeze(0).to(self.device)
            
            # Scale 2: Center crop
            crop_img = self._center_crop_numpy(image)
            input_tensor_crop = self.transform(crop_img).unsqueeze(0).to(self.device)
            
            if self.onnx_session is not None:
                inp_full = input_tensor_full.cpu().numpy()
                inp_crop = input_tensor_crop.cpu().numpy()
                
                ort_inputs_full = {self.onnx_session.get_inputs()[0].name: inp_full}
                ort_inputs_crop = {self.onnx_session.get_inputs()[0].name: inp_crop}
                
                emb_full = self.onnx_session.run(None, ort_inputs_full)[0]
                emb_crop = self.onnx_session.run(None, ort_inputs_crop)[0]
                
                embedding = np.concatenate([emb_full, emb_crop], axis=1)
                norm = np.linalg.norm(embedding, axis=1, keepdims=True)
                embedding = embedding / (norm + 1e-6)
                return embedding[0]
            else:
                emb_full = self.backbone(input_tensor_full)
                emb_crop = self.backbone(input_tensor_crop)
                
                embedding = torch.cat([emb_full, emb_crop], dim=1)
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
            # Batch transform for multi-scale
            batch_tensors_full = []
            batch_tensors_crop = []
            for img in valid_images:
                batch_tensors_full.append(self.transform(img))
                batch_tensors_crop.append(self.transform(self._center_crop_numpy(img)))
            
            input_tensor_full = torch.stack(batch_tensors_full).to(self.device)
            input_tensor_crop = torch.stack(batch_tensors_crop).to(self.device)

            if self.onnx_session is not None:
                inp_full = input_tensor_full.cpu().numpy()
                inp_crop = input_tensor_crop.cpu().numpy()
                
                ort_inputs_full = {self.onnx_session.get_inputs()[0].name: inp_full}
                ort_inputs_crop = {self.onnx_session.get_inputs()[0].name: inp_crop}
                
                emb_full = self.onnx_session.run(None, ort_inputs_full)[0]
                emb_crop = self.onnx_session.run(None, ort_inputs_crop)[0]
                
                embeddings_np = np.concatenate([emb_full, emb_crop], axis=1)
                norm = np.linalg.norm(embeddings_np, axis=1, keepdims=True)
                embeddings_np = embeddings_np / (norm + 1e-6)
            else:
                emb_full = self.backbone(input_tensor_full)
                emb_crop = self.backbone(input_tensor_crop)

                embeddings = torch.cat([emb_full, emb_crop], dim=1)
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
                embeddings_np = embeddings.cpu().numpy()

            # Map back to original indices
            for i, idx in enumerate(indices):
                embeddings_map[idx] = embeddings_np[i][:128]

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
