// frontend/components/landing/FeaturesSection.tsx
import React from 'react';
import { Brain, MapPin, Car, LineChart, Cloud, Shield, Disc } from 'lucide-react'; // Dot can be an alternative for Disc

interface FeaturesSectionProps {
  addToScrollRefs: (el: HTMLElement | null) => void;
}

const FeaturesSection: React.FC<FeaturesSectionProps> = ({ addToScrollRefs }) => {
  return (
    <section id="features" className="py-20 bg-background">
      <div className="container mx-auto px-4">
        <div ref={addToScrollRefs} className="text-center mb-16 scroll-transition">
          <h2 className="text-primary text-3xl md:text-4xl font-bold mb-4 matrix-glow tracking-normal">SMART TRAFFIC SOLUTIONS</h2> {/* Added tracking-normal */}
          <p className="text-muted-foreground max-w-2xl mx-auto tracking-normal"> {/* Added tracking-normal */}
            Our platform integrates cutting-edge technologies to deliver unparalleled traffic management capabilities.
          </p>
        </div>

        <div className="feature-grid grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          {/* Feature Card 1 */}
          <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition">
            <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
              <Brain size={32} className="text-primary" /> {/* Replaced fa-brain, adjusted size (text-2xl is approx 24px, Lucide default is 24px. size={32} is closer to a larger icon) */}
            </div>
            <h3 className="text-primary text-xl font-bold mb-3 tracking-normal">AI-Powered Analytics</h3> {/* Added tracking-normal */}
            <p className="text-muted-foreground mb-4 tracking-normal"> {/* Added tracking-normal */}
              Predictive algorithms analyze traffic patterns in real-time to anticipate and prevent congestion.
            </p>
            <ul className="text-card-foreground text-sm space-y-2 mb-4 tracking-normal"> {/* Added tracking-normal */}
              <li className="flex items-center">
                <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Real-time predictive modeling {/* Replaced fa-circle */}
              </li>
              <li className="flex items-center">
                <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Up to 30% congestion foresight {/* Replaced fa-circle */}
              </li>
            </ul>
            <div className="feature-underline"></div>
          </div>
          {/* Feature Card 2 */}
          <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-100">
           <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
               <MapPin size={32} className="text-primary" /> {/* Replaced fa-map-marked-alt */}
           </div>
           <h3 className="text-primary text-xl font-bold mb-3 tracking-normal">Dynamic Routing</h3> {/* Added tracking-normal */}
           <p className="text-muted-foreground mb-4 tracking-normal"> {/* Added tracking-normal */}
              Adjusts traffic light timing and suggests optimal routes based on current conditions.
           </p>
           <ul className="text-card-foreground text-sm space-y-2 mb-4 tracking-normal"> {/* Added tracking-normal */}
               <li className="flex items-center">
                   <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Adaptive signal control {/* Replaced fa-circle */}
               </li>
               <li className="flex items-center">
                   <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Multi-modal route optimization {/* Replaced fa-circle */}
               </li>
           </ul>
           <div className="feature-underline"></div>
          </div>
          {/* Feature Card 3 */}
          <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-200">
           <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
               <Car size={32} className="text-primary" /> {/* Replaced fa-car-side */}
           </div>
           <h3 className="text-primary text-xl font-bold mb-3 tracking-normal">Vehicle-to-Infrastructure</h3> {/* Added tracking-normal */}
           <p className="text-muted-foreground mb-4 tracking-normal"> {/* Added tracking-normal */}
               Seamless communication between vehicles and traffic systems for coordinated movement.
           </p>
           <ul className="text-card-foreground text-sm space-y-2 mb-4 tracking-normal"> {/* Added tracking-normal */}
               <li className="flex items-center">
                   <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Real-time vehicle data integration {/* Replaced fa-circle */}
               </li>
               <li className="flex items-center">
                   <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Priority for emergency vehicles {/* Replaced fa-circle */}
               </li>
           </ul>
           <div className="feature-underline"></div>
          </div>
          {/* Feature Card 4 */}
          <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-300">
             <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
                 <LineChart size={32} className="text-primary" /> {/* Replaced fa-chart-line */}
             </div>
             <h3 className="text-primary text-xl font-bold mb-3 tracking-normal">Real-Time Monitoring</h3> {/* Added tracking-normal */}
             <p className="text-muted-foreground mb-4 tracking-normal"> {/* Added tracking-normal */}
                 Comprehensive dashboard with live traffic data from sensors, cameras, and GPS sources.
             </p>
             <ul className="text-card-foreground text-sm space-y-2 mb-4 tracking-normal"> {/* Added tracking-normal */}
                 <li className="flex items-center">
                     <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> City-wide traffic visualization {/* Replaced fa-circle */}
                 </li>
                 <li className="flex items-center">
                     <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Incident detection alerts {/* Replaced fa-circle */}
                 </li>
             </ul>
             <div className="feature-underline"></div>
         </div>
         {/* Feature Card 5 */}
         <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-[400ms]">
             <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
                 <Cloud size={32} className="text-primary" /> {/* Replaced fa-cloud */}
             </div>
             <h3 className="text-primary text-xl font-bold mb-3 tracking-normal">Cloud-Based Platform</h3> {/* Added tracking-normal */}
             <p className="text-muted-foreground mb-4 tracking-normal"> {/* Added tracking-normal */}
                 Scalable infrastructure that grows with your city&lsquo;s needs, accessible from anywhere.
             </p>
             <ul className="text-card-foreground text-sm space-y-2 mb-4 tracking-normal"> {/* Added tracking-normal */}
                 <li className="flex items-center">
                     <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> 99.99% uptime SLA {/* Replaced fa-circle */}
                 </li>
                 <li className="flex items-center">
                     <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Multi-city deployment support {/* Replaced fa-circle */}
                 </li>
             </ul>
             <div className="feature-underline"></div>
         </div>
         {/* Feature Card 6 */}
         <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-500">
             <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
                 <Shield size={32} className="text-primary" /> {/* Replaced fa-shield-alt */}
             </div>
             <h3 className="text-primary text-xl font-bold mb-3 tracking-normal">Cybersecurity Focus</h3> {/* Added tracking-normal */}
             <p className="text-muted-foreground mb-4 tracking-normal"> {/* Added tracking-normal */}
                 Military-grade encryption protects critical infrastructure from digital threats.
             </p>
             <ul className="text-card-foreground text-sm space-y-2 mb-4 tracking-normal"> {/* Added tracking-normal */}
                 <li className="flex items-center">
                     <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> FIPS 140-2 compliant {/* Replaced fa-circle */}
                 </li>
                 <li className="flex items-center">
                     <Disc className="mr-2 h-2 w-2 text-primary fill-current" /> Continuous threat monitoring {/* Replaced fa-circle */}
                 </li>
             </ul>
             <div className="feature-underline"></div>
         </div>
        </div>
      </div>
    </section>
  );
};

export default FeaturesSection;
