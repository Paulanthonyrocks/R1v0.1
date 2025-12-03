import cv2
import sys

video_path = "backend/data/sample_traffic.mp4"

cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print(f"Error: Could not open {video_path}")
    sys.exit(1)

total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
print(f"Total frames: {total_frames}")

count = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break
    count += 1
    if count % 100 == 0:
        print(f"Read {count} frames...")

print(f"Actually read {count} frames.")
cap.release()
