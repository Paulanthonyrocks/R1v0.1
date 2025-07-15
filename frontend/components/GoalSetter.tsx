import React, { useState } from 'react';

const GoalSetter = () => {
    const [goalType, setGoalType] = useState('reduce_congestion');
    const [objectives, setObjectives] = useState([{ kpi: '', target_value: '', weight: 1.0 }]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);

    const handleObjectiveChange = (index, field, value) => {
        const newObjectives = [...objectives];
        newObjectives[index][field] = value;
        setObjectives(newObjectives);
    };

    const handleAddObjective = () => {
        setObjectives([...objectives, { kpi: '', target_value: '', weight: 1.0 }]);
    };

    const handleRemoveObjective = (index) => {
        const newObjectives = [...objectives];
        newObjectives.splice(index, 1);
        setObjectives(newObjectives);
    };

    const handleSetGoal = async () => {
        setIsLoading(true);
        setError(null);
        setSuccess(null);

        try {
            const response = await fetch('/api/v1/goals', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    id: 'goal-' + Date.now(),
                    description: goalType,
                    objectives: objectives,
                }),
            });

            if (!response.ok) {
                throw new Error('Failed to set goal');
            }

            const data = await response.json();
            setSuccess(`Goal set successfully: ${data.description}`);
            setObjectives([{ kpi: '', target_value: '', weight: 1.0 }]);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setIsLoading(false);
        }
    };

    const handleClearGoal = async () => {
        setIsLoading(true);
        setError(null);
        setSuccess(null);

        try {
            const response = await fetch('/api/v1/goals', {
                method: 'DELETE',
            });

            if (!response.ok) {
                throw new Error('Failed to clear goal');
            }

            const data = await response.json();
            setSuccess(data.message);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="p-4 border rounded-lg bg-card text-matrix border-primary pixel-drop-shadow">
            <h2 className="text-xl font-bold mb-4 tracking-normal">Set Agent Goal</h2>
            <div className="flex items-center space-x-2 mb-4">
                <label htmlFor="goal-type" className="font-bold tracking-normal">Goal Type:</label>
                <select
                    id="goal-type"
                    value={goalType}
                    onChange={(e) => setGoalType(e.target.value)}
                    className="border p-2 rounded-md bg-background text-matrix border-primary"
                >
                    <option value="reduce_congestion">Reduce Congestion</option>
                    <option value="clear_incident">Clear Incident</option>
                </select>
            </div>
            {objectives.map((objective, index) => (
                <div key={index} className="flex items-center space-x-2 mb-2">
                    <input
                        type="text"
                        value={objective.kpi}
                        onChange={(e) => handleObjectiveChange(index, 'kpi', e.target.value)}
                        className="border p-2 rounded-md w-1/3 bg-background text-matrix border-primary"
                        placeholder="KPI"
                    />
                    <input
                        type="text"
                        value={objective.target_value}
                        onChange={(e) => handleObjectiveChange(index, 'target_value', e.target.value)}
                        className="border p-2 rounded-md w-1/3 bg-background text-matrix border-primary"
                        placeholder="Target Value"
                    />
                    <input
                        type="number"
                        value={objective.weight}
                        onChange={(e) => handleObjectiveChange(index, 'weight', parseFloat(e.target.value))}
                        className="border p-2 rounded-md w-1/6 bg-background text-matrix border-primary"
                        placeholder="Weight"
                    />
                    <button
                        onClick={() => handleRemoveObjective(index)}
                        className="bg-red-500 text-white p-2 rounded-md"
                    >
                        Remove
                    </button>
                </div>
            ))}
            <button
                onClick={handleAddObjective}
                className="bg-gray-500 text-white p-2 rounded-md mb-4"
            >
                Add Objective
            </button>
            <div className="flex items-center space-x-2">
                <button
                    onClick={handleSetGoal}
                    disabled={isLoading}
                    className="bg-blue-500 text-white p-2 rounded-md disabled:bg-gray-400"
                >
                    {isLoading ? 'Setting...' : 'Set Goal'}
                </button>
                <button
                    onClick={handleClearGoal}
                    disabled={isLoading}
                    className="bg-red-500 text-white p-2 rounded-md disabled:bg-gray-400"
                >
                    {isLoading ? 'Clearing...' : 'Clear Goal'}
                </button>
            </div>
            {error && <p className="text-red-500 mt-2">{error}</p>}
            {success && <p className="text-green-500 mt-2">{success}</p>}
        </div>
    );
};

export default GoalSetter;
