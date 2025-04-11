// components/dashboard/ReportAnomalyModal.tsx
import React, { useState, useEffect } from 'react';
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

// Define severity options available for reporting
const severityOptions: { value: SeverityLevel; label: string }[] = [
  { value: 'Critical', label: 'Critical' },
  { value: 'Warning', label: 'Warning' },
  { value: 'Anomaly', label: 'Anomaly' },
  { value: 'INFO', label: 'Info' },
];

const ReportAnomalyModal = ({ open, onOpenChange, onSubmit }: ReportAnomalyModalProps) => {
  // Initial form state
  const initialFormData = {
    message: '',
    severity: 'Anomaly' as SeverityLevel, // Default severity
    description: '',
    location: '',
  };
  const [formData, setFormData] = useState(initialFormData);
  const [error, setError] = useState<string | null>(null);

  // Reset form when modal opens/closes
  useEffect(() => {
    if (!open) {
      setFormData(initialFormData);
      setError(null);
    }
  }, [open]);

  // Generic handler for input/textarea changes
  const handleInputChange = (field: keyof typeof formData) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setFormData(prev => ({ ...prev, [field]: event.target.value }));
    // Clear error if the required field is being typed into
    if (field === 'message' && event.target.value.trim()) {
      setError(null);
    }
  };

  // Handler for Select component change
  const handleSelectChange = (value: string) => {
     // Assert value is a SeverityLevel - use guard if necessary
    setFormData(prev => ({ ...prev, severity: value as SeverityLevel }));
  };


  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Basic validation
    if (!formData.message.trim()) {
      setError('Message field is required.');
      return;
    }
    setError(null); // Clear error on successful validation

    // Prepare data for submission (omit empty optional fields)
    const submissionData = {
      message: formData.message.trim(),
      severity: formData.severity,
      ...(formData.description.trim() && { description: formData.description.trim() }),
      ...(formData.location.trim() && { location: formData.location.trim() }),
    };

    console.log('Reporting anomaly (frontend):', submissionData);
    // Call the onSubmit prop if provided (this would trigger the API call)
    if (onSubmit) {
      onSubmit(submissionData);
    }

    // Close the modal after submission attempt
    onOpenChange(false);
    // Form state will be reset by useEffect when `open` becomes false
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px] bg-card border-border text-foreground p-6">
        <DialogHeader className="mb-4 text-left"> {/* Ensure left alignment */}
          <DialogTitle className="flex items-center gap-2 text-lg font-semibold">
            <AlertTriangle className="h-5 w-5 text-primary" />
            Report Traffic Anomaly
          </DialogTitle>
          <DialogDescription className="text-muted-foreground pt-1">
            Submit details about a traffic event or observation. Provide a clear message.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Message Field (Required) */}
          <div className="space-y-1.5">
            <Label htmlFor="message">Message <span className="text-destructive">*</span></Label>
            <Input
              id="message"
              value={formData.message}
              onChange={handleInputChange('message')}
              placeholder="e.g., Standstill traffic on Main St southbound"
              required
              aria-required="true"
              className={cn(error && 'border-destructive focus-visible:ring-destructive')}
            />
            {error && <p className="text-xs text-destructive pt-1">{error}</p>}
          </div>

          {/* Severity Field */}
          <div className="space-y-1.5">
            <Label htmlFor="severity">Severity</Label>
            {/* Ensure Select component from Shadcn is correctly imported and used */}
            <Select value={formData.severity} onValueChange={handleSelectChange}>
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

          {/* Description Field (Optional) */}
          <div className="space-y-1.5">
            <Label htmlFor="description">Description (Optional)</Label>
            <Textarea
              id="description"
              value={formData.description}
              onChange={handleInputChange('description')}
              placeholder="Add any extra details..."
              rows={3}
              className="resize-y min-h-[60px]" // Allow vertical resize
            />
          </div>

          {/* Location Field (Optional) */}
          <div className="space-y-1.5">
            <Label htmlFor="location">Location (Optional)</Label>
            <Input
              id="location"
              value={formData.location}
              onChange={handleInputChange('location')}
              placeholder="e.g., Near Main St & 5th Ave intersection"
            />
          </div>

          {/* Footer Buttons */}
          <DialogFooter className="mt-6 sm:justify-end"> {/* Adjusted spacing and alignment */}
            <DialogClose asChild>
              <Button type="button" variant="secondary">Cancel</Button>
            </DialogClose>
            <Button type="submit" variant="default">Submit Report</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

export default ReportAnomalyModal;