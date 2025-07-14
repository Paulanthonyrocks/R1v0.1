import React, { useState } from 'react';

const GoalSetter = () => {
    const [goal, setGoal] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);

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
                    description: goal,
                }),
            });

            if (!response.ok) {
                throw new Error('Failed to set goal');
            }

            const data = await response.json();
            setSuccess(`Goal set successfully: ${data.description}`);
            setGoal('');
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
        <div className="p-4 border rounded-lg">
            <h2 className="text-xl font-bold mb-4">Set Agent Goal</h2>
            <div className="flex items-center space-x-2">
                <input
                    type="text"
                    value={goal}
                    onChange={(e) => setGoal(e.target.value)}
                    className="border p-2 rounded-md w-full"
                    placeholder="e.g., Reduce congestion on Main St"
                />
                <button
                    onClick={handleSetGoal}
                    disabled={isLoading || !goal}
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
