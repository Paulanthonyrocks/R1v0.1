import { NextApiRequest, NextApiResponse } from 'next';

// Placeholder data for anomalies
const anomalies = [
  { id: 'anomaly-1', type: 'high_traffic', timestamp: '2023-10-27T10:00:00Z', status: 'active' },
  { id: 'anomaly-2', type: 'low_conversion_rate', timestamp: '2023-10-27T10:15:00Z', status: 'active' },
  { id: 'anomaly-3', type: 'server_error_spike', timestamp: '2023-10-27T10:30:00Z', status: 'resolved' },
];

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === 'GET') {
    res.status(200).json(anomalies);
  } else {
    res.setHeader('Allow', ['GET']);
    res.status(405).end(`Method ${req.method} Not Allowed`);
  }
}