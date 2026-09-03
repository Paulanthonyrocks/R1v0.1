// components/dashboard/ReportAnomalyModal.tsx
import React, { useState, useEffect, useMemo } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog"; // Using double quotes here is also fine
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input"; // Use standard quote ' or "
import { Textarea } from "../ui/textarea"; // Use standard quote ' or "
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"; // Use standard quote ' or "
import { Label } from "@/components/ui/label"; // Use standard quote ' or "
import { AlertTriangle } from 'lucide-react';
import { cn } from "@/lib/utils";
import { ReportAnomalyModalProps, SeverityLevel } from '@/lib/types'
import { incidentService } from '@/lib/services/incidentService';

// Define severity options available for reporting
const severityOptions: { value: SeverityLevel; label: string }[] = [
  { value: 'Critical', label: 'Critical' },
  { value: 'Warning', label: 'Warning' },
  { value: 'Anomaly', label: 'General Anomaly' },
  { value: 'INFO', label: 'Information' },
];

const ReportAnomalyModal = ({ open, onOpenChange, onSubmit }: ReportAnomalyModalProps) => {
  // Initial form state
  const initialFormData = useMemo(() => ({
    type: '',
    severity: 'Anomaly' as SeverityLevel, // Default severity
    description: '',
    location: '',
  }), []);

  const [formData, setFormData] = useState(initialFormData);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Reset form when modal opens/closes
  useEffect(() => {
    if (!open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- form reset on open/close transition; prop-change reset has no render-time equivalent
      setFormData(initialFormData);
      setError(null);
    }
  }, [open, initialFormData]);

  // Generic handler for input/textarea changes
  const handleInputChange = (field: keyof typeof formData) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setFormData((prev: typeof formData) => ({ ...prev, [field]: event.target.value }));
    // Clear error if the required field is being typed into
    if (field === 'type' && event.target.value.trim()) {
      setError(null);
    }
  };

  // Handler for Select component change
  const handleSelectChange = (value: string) => {
    setFormData((prev: typeof formData) => ({ ...prev, severity: value as SeverityLevel }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.type.trim()) {
      setError('Incident type/title is required.');
      return;
    }

    setIsSubmitting(true);
    setError(null);

    const submissionData = {
      type: formData.type.trim(),
      severity: formData.severity,
      description: formData.description.trim() || `Manual report: ${formData.type}`,
      // In a real app, we might parse location or get coordinates from a map
      latitude: 0,
      longitude: 0,
    };

    try {
      const result = await incidentService.createIncident(submissionData);
      if (result && onSubmit) {
        // Trigger local update if needed, though WebSocket should handle it
        onSubmit({
          message: submissionData.type,
          severity: submissionData.severity as SeverityLevel,
          description: submissionData.description,
          location: formData.location
        });
      }
      onOpenChange(false);
    } catch (err) {
      setError('Failed to submit incident. Please try again.');
      console.error(err);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px] bg-card border-border text-foreground p-6">
        <DialogHeader className="mb-4 text-left">
          <DialogTitle className="flex items-center gap-2 text-lg font-semibold">
            <AlertTriangle className="h-5 w-5 text-destructive" />
            Report New Incident
          </DialogTitle>
          <DialogDescription className="text-muted-foreground pt-1">
            Manually trigger an incident report for operator review.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="type">Incident Type / Title <span className="text-destructive">*</span></Label>
            <Input
              id="type"
              value={formData.type}
              onChange={handleInputChange('type')}
              placeholder="e.g., Road Blockage, Signal Failure"
              required
              disabled={isSubmitting}
              className={cn(error && 'border-destructive focus-visible:ring-destructive')}
            />
            {error && <p className="text-xs text-destructive pt-1">{error}</p>}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="severity">Severity Level</Label>
            <Select value={formData.severity} onValueChange={handleSelectChange} disabled={isSubmitting}>
              <SelectTrigger id="severity" className="w-full">
                <SelectValue placeholder="Select severity..." />
              </SelectTrigger>
              <SelectContent>
                {severityOptions.map(option => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="description">Detailed Description</Label>
            <Textarea
              id="description"
              value={formData.description}
              onChange={handleInputChange('description')}
              placeholder="Provide specific details about the observed incident..."
              rows={3}
              disabled={isSubmitting}
              className="resize-y min-h-[80px]"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="location">Location Reference</Label>
            <Input
              id="location"
              value={formData.location}
              onChange={handleInputChange('location')}
              placeholder="e.g., Junction 4, Southbound"
              disabled={isSubmitting}
            />
          </div>

          <DialogFooter className="mt-6 sm:justify-end gap-2">
            <DialogClose asChild>
              <Button type="button" variant="secondary" disabled={isSubmitting}>Cancel</Button>
            </DialogClose>
            <Button type="submit" variant="destructive" disabled={isSubmitting}>
              {isSubmitting ? 'Submitting...' : 'Broadcast Incident'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

export default ReportAnomalyModal;