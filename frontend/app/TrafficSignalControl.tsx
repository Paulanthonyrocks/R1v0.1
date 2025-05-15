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
    <div>
      <h1>Traffic Signal Control</h1>
      <div>
        <label htmlFor="signal">Select Signal:</label>
        <select
          id="signal"
          value={selectedSignal || ''}
          onChange={(e) => setSelectedSignal(e.target.value)}
        >
          <option value="" disabled>Select a signal</option>
          {signals.map((signal) => (
            <option key={signal.id} value={signal.id}>{signal.id}</option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="phase">Set Phase:</label>
        <select
          id="phase"
          value={phase}
          onChange={(e) => setPhase(e.target.value)}
        >
          <option value="" disabled>Select a phase</option>
          <option value="green">Green</option>
          <option value="yellow">Yellow</option>
          <option value="red">Red</option>
        </select>
      </div>
      <button onClick={updateSignalPhase}>Update Phase</button>
    </div>
  );
};

export default TrafficSignalControl;