import React, { useState, useEffect, useCallback } from 'react';
import { 
    Dialog, 
    DialogContent, 
    DialogHeader, 
    DialogTitle, 
    DialogDescription,
    DialogFooter 
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Plus, Loader2 } from 'lucide-react';
import useAuth from '@/lib/hook/useAuth';

function AddFeedDialog() {
    const { token } = useAuth();
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [formData, setFormData] = useState({
        source: '',
        name: '',
        latitude: '',
        longitude: ''
    });

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setFormData(prev => ({ ...prev, [e.target.id]: e.target.value }));
    };

    const handleOpen = useCallback((e: React.MouseEvent | React.PointerEvent) => {
        console.log("[AddFeedDialog] handleOpen triggered");
        e.preventDefault();
        e.stopPropagation();
        setOpen(true);
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        e.stopPropagation();
        console.log("[AddFeedDialog] Submitting form...");
        setLoading(true);
        setError(null);

        try {
            const body: any = {
                source: formData.source,
                name: formData.name || undefined
            };
            
            if (formData.latitude) body.latitude = parseFloat(formData.latitude);
            if (formData.longitude) body.longitude = parseFloat(formData.longitude);

            const res = await fetch('/api/v1/feeds/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`,
                    'Bypass-Tunnel-Reminder': 'true'
                },
                body: JSON.stringify(body)
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || 'Failed to add feed');
            }

            console.log("[AddFeedDialog] Success, closing dialog");
            setOpen(false);
            setFormData({ source: '', name: '', latitude: '', longitude: '' });
        } catch (err: any) {
            console.error("[AddFeedDialog] Error:", err.message);
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <>
            <Button 
                type="button"
                variant="outline" 
                className="bg-lcd-text/10 border-lcd-text text-lcd-text hover:bg-lcd-text hover:text-lcd-bg rounded-none uppercase font-bold"
                onClick={handleOpen}
                onPointerDown={(e) => e.stopPropagation()}
                onMouseDown={(e) => e.stopPropagation()}
            >
                <Plus className="mr-2 h-4 w-4" /> Add Feed
            </Button>

            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent 
                    className="sm:max-w-[425px]"
                    onPointerDownOutside={(e) => {
                        // If the user clicks the trigger again while it's open, 
                        // prevent radix from closing it immediately so our button logic handles it
                        if (e.target instanceof Element && e.target.closest('button')?.textContent?.includes('Add Feed')) {
                            e.preventDefault();
                        }
                    }}
                >
                    <DialogHeader>
                        <DialogTitle>Add New Feed</DialogTitle>
                        <DialogDescription>
                            Enter the source details for the new traffic monitoring node.
                        </DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleSubmit} className="grid gap-4 py-4">
                        {error && <div className="text-red-500 text-sm font-lcd matrix-glow">{error}</div>}
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="source" className="text-right">
                                Source
                            </Label>
                            <Input
                                id="source"
                                placeholder="RTSP URL, File path..."
                                className="col-span-3 rounded-none bg-lcd-bg border-lcd-text text-lcd-text focus-visible:ring-lcd-text"
                                value={formData.source}
                                onChange={handleChange}
                                required
                            />
                        </div>
                        <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="name" className="text-right">
                                Name
                            </Label>
                            <Input
                                id="name"
                                placeholder="Optional name"
                                className="col-span-3 rounded-none bg-lcd-bg border-lcd-text text-lcd-text focus-visible:ring-lcd-text"
                                value={formData.name}
                                onChange={handleChange}
                            />
                        </div>
                         <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="latitude" className="text-right">
                                Lat
                            </Label>
                            <Input
                                id="latitude"
                                type="number"
                                step="any"
                                placeholder="Latitude"
                                className="col-span-3 rounded-none bg-lcd-bg border-lcd-text text-lcd-text focus-visible:ring-lcd-text"
                                value={formData.latitude}
                                onChange={handleChange}
                            />
                        </div>
                         <div className="grid grid-cols-4 items-center gap-4">
                            <Label htmlFor="longitude" className="text-right">
                                Lon
                            </Label>
                            <Input
                                id="longitude"
                                type="number"
                                step="any"
                                placeholder="Longitude"
                                className="col-span-3 rounded-none bg-lcd-bg border-lcd-text text-lcd-text focus-visible:ring-lcd-text"
                                value={formData.longitude}
                                onChange={handleChange}
                            />
                        </div>
                        <DialogFooter>
                            <Button type="submit" disabled={loading} className="rounded-none bg-lcd-text text-lcd-bg hover:bg-lcd-text/80 w-full sm:w-auto">
                                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Add Feed
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </>
    );
}

export default React.memo(AddFeedDialog);