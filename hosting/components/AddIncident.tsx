// frontend/components/AddIncident.tsx
import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { createIncident } from '@/lib/firebase/firestore';

interface AddIncidentForm {
  area: string;
  description: string;
}

const AddIncident: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [formData, setFormData] = useState<AddIncidentForm>({
    area: '',
    description: '',
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createIncident(formData);
      setFormData({ area: '', description: '' });
      setIsOpen(false);
    } catch (error) {
      console.error('Error creating incident:', error);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button>Add Incident</Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Add New Incident</DialogTitle>
          <DialogDescription>
            Fill the form to add a new incident.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-2">
            <Label htmlFor="area">Area</Label>
            <Input
              id="area"
              name="area"
              value={formData.area}
              onChange={handleChange}
              required
              placeholder="Enter the area"
              className="border border-gray-300 rounded-md px-3 py-2"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              name="description"
              value={formData.description}
              onChange={handleChange}
              required
              placeholder="Enter the description"
              className="border border-gray-300 rounded-md px-3 py-2"
            />
          </div>
          <DialogFooter>
            <Button type="submit">Add Incident</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

export default AddIncident;