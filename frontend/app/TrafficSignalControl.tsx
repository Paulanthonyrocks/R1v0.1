import React, { useState, useEffect } from 'react';

interface Signal {
  id: string;
}

const TrafficSignalControl = () => {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [selectedSignal, setSelectedSignal] = useState<string | null>(null);
  const [phase, setPhase] = useState('');

  useEffect(() => {
    // Fetch the list of traffic signals
    const fetchSignals = async () => {
      try {
        const response = await fetch('/api/v1/signals');
        const data = await response.json();
        setSignals(data);
      } catch (error) {
        console.error('Error fetching signals:', error);
      }
    };

    fetchSignals();
  }, []);

  const updateSignalPhase = async () => {
    if (!selectedSignal || !phase) return;

    try {
      const response = await fetch(`/api/v1/signals/${selectedSignal}/set_phase`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phase }),
      });

      if (response.ok) {
        alert('Signal phase updated successfully');
      } else {
        alert('Failed to update signal phase');
      }
    } catch (error) {
      console.error('Error updating signal phase:', error);
    }
  };

  return (
    <div className="matrix-glow-card p-8 max-w-md mx-auto rounded-none">
      <h1 className="text-xl font-bold mb-4 text-primary font-lcd">Traffic Signal Control</h1>
      <div className="mb-4">
        <label htmlFor="signal" className="block text-sm font-semibold mb-2 text-primary font-lcd">Select Signal:</label>
        <select
          id="signal"
          value={selectedSignal || ''}
          onChange={(e) => setSelectedSignal(e.target.value)}
          className="matrix-input w-full font-lcd"
        >
          <option value="" disabled className="font-lcd">Select a signal</option>
          {signals.map((signal) => (
            <option key={signal.id} value={signal.id} className="font-lcd">{signal.id}</option>
          ))}
        </select>
      </div>
      <div className="mb-6">
        <label htmlFor="phase" className="block text-sm font-semibold mb-2 text-primary font-lcd">Set Phase:</label>
        <select
          id="phase"
          value={phase}
          onChange={(e) => setPhase(e.target.value)}
          className="matrix-input w-full font-lcd"
        >
          <option value="" disabled className="font-lcd">Select a phase</option>
          <option value="green" className="font-lcd">Green</option>
          <option value="yellow" className="font-lcd">Yellow</option>
          <option value="red" className="font-lcd">Red</option>
        </select>
      </div>
      <button onClick={updateSignalPhase} className="matrix-button w-full">Update Phase</button>
    </div>
  );
};

export default TrafficSignalControl;