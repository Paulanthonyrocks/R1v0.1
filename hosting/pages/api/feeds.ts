import { promises as fs } from 'fs';
import { NextApiRequest, NextApiResponse } from 'next';

interface Location {
  lat: number;
  long: number;
}

interface Feed {
  id: number;
  name: string;
  location: Location;
  timestamp: string;
  status: string;
  details: string;
}

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse<Feed[]>
) {
  try {
    const data = await fs.readFile('./data.json', 'utf8');
    const feeds: Feed[] = JSON.parse(data);
    res.status(200).json(feeds);
  } catch (error) {
    console.error('Error reading or parsing data.json:', error);
    res.status(500).end();
  }
}