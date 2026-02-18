import type { NextApiRequest, NextApiResponse } from 'next';
import path from 'path';
import fs from 'fs';

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  const videoPath = path.join(process.cwd(), 'public', 'sample_traffic.mp4');
  if (!fs.existsSync(videoPath)) {
    res.status(404).json({ error: 'Video not found' });
    return;
  }
  const stat = fs.statSync(videoPath);
  res.writeHead(200, {
    'Content-Type': 'video/mp4',
    'Content-Length': stat.size,
  });
  const readStream = fs.createReadStream(videoPath);
  readStream.pipe(res);
}
