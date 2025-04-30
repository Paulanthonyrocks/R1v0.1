import { MatrixCard } from '@/components/MatrixCard';
import { useEffect, useState } from 'react';
import { MatrixButton } from '@/components/MatrixButton';

export default function Home() {
  const [matrixLines, setMatrixLines] = useState<string[]>([]);

  useEffect(() => {
    const numLines = 30;
    const newLines = Array.from({ length: numLines }, (_, index) => {
      const lineLength = Math.floor(Math.random() * 20) + 5;
      return Array.from({ length: lineLength }, () =>
        Math.random() < 0.5 ? '0' : '1'
      ).join('');
    });
    setMatrixLines(newLines);

    const intervalId = setInterval(() => {
      setMatrixLines((prevLines) => {
        const updatedLines = prevLines.map((line) => {
          const lineLength = line.length;
          return Array.from({ length: lineLength }, () =>
            Math.random() < 0.5 ? '0' : '1'
          ).join('');
        });
        return updatedLines;
      });
    }, 500);

    return () => clearInterval(intervalId);
  }, []);

  return (
    <div className="h-screen bg-matrix-bg flex flex-col items-center justify-center overflow-hidden relative">
      <div
        className="absolute inset-0 z-0 pointer-events-none overflow-hidden"
        aria-hidden="true"
      >
        <div className="absolute inset-0 grid grid-cols-30 animate-matrix gap-2 font-matrix">
          {matrixLines.map((line, index) => (
            <div key={index} className="text-matrix-text text-sm">
              {line}
            </div>
          ))}
        </div>
      </div>
      <main className="relative z-10 h-full flex items-center justify-center w-full">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 p-4">
          <MatrixCard title="Card 1" content="This is the content of Card 1." />
          <MatrixCard title="Card 2" content="This is the content of Card 2." colorOverride="--matrix-light" />
          <MatrixCard title="Card 3" content="This is the content of Card 3." />
          <MatrixCard title="Card 4" content="This is the content of Card 4." colorOverride="--matrix-light" />
          <MatrixButton content="Button 1" />
          <MatrixButton content="Button 2" textColor="--matrix-bg" backgroundColor="--matrix" />
          <MatrixButton content="Disabled" disabled={true} />
          <MatrixButton content="Button 3" backgroundColor="--matrix-light" />
          <MatrixButton content="Button 4" textColor="--matrix-bg" />
          <MatrixButton content="Disabled" disabled={true} backgroundColor="--matrix-light" />
        </div>
      </main>
    </div>
  );
}