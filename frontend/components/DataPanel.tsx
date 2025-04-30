import React from 'react';

interface DataPanelProps {
  id: string;
  title: string;
  value: string | number;
  unit?: string;
}

const DataPanel: React.FC<DataPanelProps> = ({ id, title, value, unit }) => {
    return (
    <div className="bg-gray-100 rounded-md p-4 shadow-md flex flex-col items-center justify-center" key={id}>
      <h2 className="text-gray-600 text-sm">{title}</h2>
      <div className="flex items-center">
        <p className="text-2xl font-bold text-blue-500">{value}</p>
          <p className="text-gray-500 text-sm">{unit}</p>
      </div>
  </div>
  );
};

export default DataPanel;