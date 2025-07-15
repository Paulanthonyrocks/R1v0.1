import React from 'react';

const PlanViewer = ({ plan }) => {
    if (!plan) {
        return null;
    }

    return (
        <div className="p-4 border rounded-lg bg-card text-matrix border-primary pixel-drop-shadow">
            <h2 className="text-xl font-bold mb-4 tracking-normal">Current Plan</h2>
            <div>
                <p className="pixel-font"><span className="font-bold tracking-normal">ID:</span> {plan.id}</p>
                <p className="pixel-font"><span className="font-bold tracking-normal">Description:</span> {plan.description}</p>
                <p className="pixel-font"><span className="font-bold tracking-normal">Status:</span> {plan.status}</p>
            </div>
            <div className="mt-4">
                <h3 className="text-lg font-bold mb-2 tracking-normal">Actions</h3>
                {plan.actions.map((action, index) => (
                    <div key={index} className="p-2 border-b border-primary">
                        <p className="pixel-font"><span className="font-bold tracking-normal">Type:</span> {action.action_type}</p>
                        <p className="pixel-font"><span className="font-bold tracking-normal">Target:</span> {action.target_ids.join(', ')}</p>
                        <p className="pixel-font"><span className="font-bold tracking-normal">Parameters:</span> {JSON.stringify(action.parameters)}</p>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default PlanViewer;
