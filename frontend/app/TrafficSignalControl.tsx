import React, { useState, useEffect } from 'react';
import { Signal, Clock, BatteryFull } from 'lucide-react';
import useAuth from '@/lib/hook/useAuth'; // Import useAuth

interface Signal {
  id: string;
}

const TrafficSignalControl = () => {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [selectedSignal, setSelectedSignal] = useState<string | null>(null);
  const [phase, setPhase] = useState('');
  const { token } = useAuth(); // Get the token from useAuth

  useEffect(() => {
    // Fetch the list of traffic signals
    const fetchSignals = async () => {
      if (!token) {
        console.log("TrafficSignalControl: Token not available, skipping fetchSignals.");
        return;
      }
      try {
        const response = await fetch('/api/v1/signals', {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Bypass-Tunnel-Reminder': 'true'
          },
        });
        const data = await response.json();
        setSignals(data);
      } catch (error) {
        console.error('Error fetching signals:', error);
      }
    };

    fetchSignals();
  }, [token]); // Add token to dependency array

  const updateSignalPhase = async () => {
    if (!selectedSignal || !phase || !token) return;

    try {
      const response = await fetch(`/api/v1/signals/${selectedSignal}/set_phase`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
          'Bypass-Tunnel-Reminder': 'true'
        },
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
    <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
      {/* Status Bar */}
      <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
        <div className="flex items-center space-x-2">
          <Signal size={20} />
          <span className="font-lcd matrix-glow">SIGNAL CONTROL</span>
        </div>
        <div className="flex items-center space-x-2">
          <Clock size={20} />
          <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          <BatteryFull size={20} />
        </div>
      </header>
      <div className="flex items-center justify-center flex-1">
        <div className="matrix-card p-8 max-w-md w-full">
          <h1 className="text-xl font-bold mb-4 text-lcd-text font-lcd">Traffic Signal Control</h1>
          <div className="mb-4">
            <label htmlFor="signal" className="block text-sm font-semibold mb-2 text-lcd-text font-lcd">Select Signal:</label>
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
            <label htmlFor="phase" className="block text-sm font-semibold mb-2 text-lcd-text font-lcd">Set Phase:</label>
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
      </div>
    </div>
  );
};

export default TrafficSignalControl;
