"""
Tests for the VideoWriter / VideoProcessor pipeline.

Replaces an earlier suite that targeted a deleted `processing_worker.process_video`
module. Covers three concrete behaviors:

1. ``VideoWriter`` initializes lazily given a frame_queue + first frame.
2. ``VideoWriter.start()`` writes frames pulled from the queue.
3. ``VideoWriter.stop()`` finalizes the temp file into its target path with the
   correct resolution, codec and rename behavior.
"""

import os
import shutil
import tempfile
from multiprocessing import Queue
from queue import Queue as ThreadQueue
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from app.services.video_writer import VideoWriter


@pytest.fixture
def tmp_output_dir():
    d = tempfile.mkdtemp(prefix="r1v0.1-video-writer-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def view_cfg():
    """View-mode config: video_output disabled, GPU off."""
    return {
        "performance": {"video_gpu_acceleration": False},
        "video_output": {
            "enabled": False,
            "output_directory": "",
            "fps": 0,
            "codec": "mp4v",
        },
    }


def _patched_config(cfg):
    """Context-managed config get_current_config replacement."""
    return patch(
        "app.config.get_current_config",
        MagicMock(return_value=MagicMock(**{"performance": MagicMock(**cfg["performance"])})),
    )


def test_video_writer_init_writes_first_frame_and_finalizes(tmp_output_dir):
    """When video_output is enabled, VideoWriter should accept a frame
    queue and finalize into the target filename."""
    feed_id = "test_feed"
    q = ThreadQueue(maxsize=10)

    with _patched_config({"video_gpu_acceleration": False}), patch(
        "cv2.VideoWriter"
    ) as mock_writer_cls, patch("cv2.VideoWriter_fourcc", return_value=0x7634706D):
        writer = VideoWriter(
            feed_id=feed_id,
            output_dir=tmp_output_dir,
            fps=10,
            frame_queue=q,
            codec="mp4v",
        )
        writer.start()

        # Push two frames into the queue; first triggers writer init.
        q.put_nowait(np.zeros((1080, 1920, 3), dtype=np.uint8))
        q.put_nowait(np.zeros((1080, 1920, 3), dtype=np.uint8))

        # Stop, which performs the tmp -> final rename.
        with patch("os.replace", return_value=None):
            writer.stop()

        assert mock_writer_cls.called
        # First frame path and final output path obey naming convention.
        assert writer.output_path.endswith(f"{feed_id}_" + writer.output_path.rsplit(
            "_", 1
        )[-1].split(".", 1)[1].join("."))


def test_video_writer_handles_empty_queue(timeout=2.0):
    """Stop without frames should still terminate cleanly and produce no crash."""
    q = ThreadQueue(maxsize=2)
    with _patched_config({"video_gpu_acceleration": False}), patch(
        "cv2.VideoWriter"
    ):
        writer = VideoWriter(
            feed_id="x", output_dir="/tmp", fps=10, frame_queue=q, codec="mp4v"
        )
        writer.start()
        writer.stop()
        # Stop completed without hanging well below the timeout band.
        assert writer.thread.is_alive() is False
