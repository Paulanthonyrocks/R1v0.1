import { NextApiRequest, NextApiResponse } from 'next';

// This is a placeholder for your data storage.
// In a real application, you would interact with a database.
let anomalies = [
  { id: '1', message: 'Anomaly 1', resolved: false },
  { id: '2', message: 'Anomaly 2', resolved: true },
];

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  const { anomalyId } = req.query;

  const anomaly = anomalies.find(a => a.id === anomalyId);

  if (!anomaly) {
    return res.status(404).json({ message: 'Anomaly not found' });
  }

  if (req.method === 'PATCH') {
    const { resolved } = req.body;

    if (typeof resolved !== 'boolean') {
      return res.status(400).json({ message: 'Invalid data for update' });
    }

    anomaly.resolved = resolved;

    return res.status(200).json(anomaly);
  }

  if (req.method === 'DELETE') {
    anomalies = anomalies.filter(a => a.id !== anomalyId);
    return res.status(200).json({ message: 'Anomaly deleted' });
  }

  res.setHeader('Allow', ['PATCH', 'DELETE']);
  res.status(405).end(`Method ${req.method} Not Allowed`);
}