import React from 'react';

interface HeaderProps {
  title: string;
}

const Header: React.FC<HeaderProps> = ({ title }) => {
  return (
    <div className="bg-gray-100 p-4">
      <h1 className="text-2xl font-bold">{title}</h1>
    </div>
  );
};

export default Header;