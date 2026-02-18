import React from 'react';
import { AlertTriangle, TrendingDown } from 'lucide-react';
import { AlertData } from '@/lib/types';

interface AnomalyListProps {
    anomalies: AlertData[];
    onSelect?: (anomaly: AlertData) => void;
}

const AnomalyList: React.FC<AnomalyListProps> = ({ anomalies, onSelect }) => {
    const getStatusStyles = (status?: string) => {
        switch (status) {
            case 'RESOLVED':
                return 'text-matrix-light border-matrix-light/30 bg-matrix-light/5';
            case 'ACKNOWLEDGED':
                return 'text-warning border-warning/30 bg-warning/5';
            default:
                return 'text-destructive border-destructive/30 bg-destructive/5';
        }
    };

    return (
        <div className="h-full flex flex-col">
            <div className="flex justify-between items-center mb-4 pt-2 border-t border-lcd-text/10">
                <h3 className="text-sm font-bold tracking-widest font-lcd text-lcd-text/80">RECENT INCIDENTS</h3>
                <span className="text-[10px] uppercase opacity-50">{anomalies.length} ACTIVE</span>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar pr-1 space-y-2">
                {anomalies.length === 0 ? (
                    <div className="text-center py-4 text-xs text-lcd-text/40">
                        NO INCIDENTS DETECTED
                    </div>
                ) : (
                    anomalies.slice().reverse().map((a, i) => (
                        <div
                            key={i}
                            onClick={() => onSelect && onSelect(a)}
                            className="flex items-center gap-3 p-2 hover:bg-lcd-text/5 cursor-pointer border-l-2 border-transparent hover:border-lcd-text transition-all group"
                        >
                            <div className={a.status === 'RESOLVED' ? 'text-matrix-light' : 'text-destructive'}>
                                {a.severity === 'Critical' ? <AlertTriangle size={16} /> : <TrendingDown size={16} />}
                            </div>
                            <div className="flex-1 min-w-0">
                                <div className="flex justify-between items-center mb-0.5">
                                    <div className="flex items-center gap-2 truncate">
                                        <span className={`text-xs font-bold uppercase tracking-wider truncate transition-colors ${a.status === 'RESOLVED' ? 'text-matrix-light/70' : 'text-destructive/90 group-hover:text-destructive'}`}>
                                            {a.message}
                                        </span>
                                        {a.status && (
                                            <span className={`text-[8px] px-1 border rounded font-mono ${getStatusStyles(a.status)}`}>
                                                {a.status}
                                            </span>
                                        )}
                                    </div>
                                    <span className="text-[10px] text-lcd-text/50 font-lcd">
                                        {new Date(a.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                                    </span>
                                </div>
                                <p className="text-[10px] text-lcd-text/60 truncate uppercase">
                                    {a.description || "Unknown Incident"}
                                </p>
                            </div>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
};

export default AnomalyList;
