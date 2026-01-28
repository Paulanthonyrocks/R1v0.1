import React from 'react';
import { AlertTriangle, TrendingDown } from 'lucide-react';
import { AlertData } from '@/lib/types';

interface AnomalyListProps {
    anomalies: AlertData[];
    onSelect?: (anomaly: AlertData) => void;
}

const AnomalyList: React.FC<AnomalyListProps> = ({ anomalies, onSelect }) => {
    return (
        <div className="h-full flex flex-col">
            <div className="flex justify-between items-center mb-4 pt-2 border-t border-lcd-text/10">
                <h3 className="text-sm font-bold tracking-widest font-lcd text-lcd-text/80">RECENT ANOMALIES</h3>
                <span className="text-[10px] uppercase opacity-50">{anomalies.length} DETECTED</span>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar pr-1 space-y-2">
                {anomalies.length === 0 ? (
                    <div className="text-center py-4 text-xs text-lcd-text/40">
                        NO ANOMALIES DETECTED
                    </div>
                ) : (
                    anomalies.slice().reverse().map((a, i) => (
                        <div
                            key={i}
                            onClick={() => onSelect && onSelect(a)}
                            className="flex items-center gap-3 p-2 hover:bg-lcd-text/5 cursor-pointer border-l-2 border-transparent hover:border-lcd-text transition-all group"
                        >
                            <div className="text-destructive">
                                {a.severity === 'Critical' ? <AlertTriangle size={16} /> : <TrendingDown size={16} />}
                            </div>
                            <div className="flex-1 min-w-0">
                                <div className="flex justify-between items-center mb-0.5">
                                    <span className="text-xs font-bold uppercase tracking-wider truncate text-destructive/90 group-hover:text-destructive transition-colors">
                                        {a.message}
                                    </span>
                                    <span className="text-[10px] text-lcd-text/50 font-lcd">
                                        {new Date(a.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                                    </span>
                                </div>
                                <p className="text-[10px] text-lcd-text/60 truncate uppercase">
                                    {a.description || "Unknown Anomaly"}
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
