// frontend/components/landing/FeaturesSection.tsx
import React from 'react';

interface FeaturesSectionProps {
  addToScrollRefs: (el: HTMLElement | null) => void;
}

const FeaturesSection: React.FC<FeaturesSectionProps> = ({ addToScrollRefs }) => {
  return (
    <section id="features" className="py-20 bg-background">
      <div className="container mx-auto px-4">
        <div ref={addToScrollRefs} className="text-center mb-16 scroll-transition">
          <h2 className="text-primary text-3xl md:text-4xl font-bold mb-4 matrix-glow">SMART TRAFFIC SOLUTIONS</h2>
          <p className="text-muted-foreground max-w-2xl mx-auto">
            Our platform integrates cutting-edge technologies to deliver unparalleled traffic management capabilities.
          </p>
        </div>

        <div className="feature-grid grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          {/* Feature Card 1 */}
          <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition">
            <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
              <i className="fas fa-brain text-2xl text-primary"></i>
            </div>
            <h3 className="text-primary text-xl font-bold mb-3">AI-Powered Analytics</h3>
            <p className="text-muted-foreground mb-4">
              Predictive algorithms analyze traffic patterns in real-time to anticipate and prevent congestion.
            </p>
            <ul className="text-card-foreground text-sm space-y-2 mb-4">
              <li className="flex items-center">
                <i className="fas fa-circle text-xs mr-2 text-primary"></i> Real-time predictive modeling
              </li>
              <li className="flex items-center">
                <i className="fas fa-circle text-xs mr-2 text-primary"></i> Up to 30% congestion foresight
              </li>
            </ul>
            <div className="feature-underline"></div>
          </div>
          {/* Feature Card 2 */}
          <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-100">
           <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
               <i className="fas fa-map-marked-alt text-2xl text-primary"></i>
           </div>
           <h3 className="text-primary text-xl font-bold mb-3">Dynamic Routing</h3>
           <p className="text-muted-foreground mb-4">
              Adjusts traffic light timing and suggests optimal routes based on current conditions.
           </p>
           <ul className="text-card-foreground text-sm space-y-2 mb-4">
               <li className="flex items-center">
                   <i className="fas fa-circle text-xs mr-2 text-primary"></i> Adaptive signal control
               </li>
               <li className="flex items-center">
                   <i className="fas fa-circle text-xs mr-2 text-primary"></i> Multi-modal route optimization
               </li>
           </ul>
           <div className="feature-underline"></div>
          </div>
          {/* Feature Card 3 */}
          <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-200">
           <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
               <i className="fas fa-car-side text-2xl text-primary"></i>
           </div>
           <h3 className="text-primary text-xl font-bold mb-3">Vehicle-to-Infrastructure</h3>
           <p className="text-muted-foreground mb-4">
               Seamless communication between vehicles and traffic systems for coordinated movement.
           </p>
           <ul className="text-card-foreground text-sm space-y-2 mb-4">
               <li className="flex items-center">
                   <i className="fas fa-circle text-xs mr-2 text-primary"></i> Real-time vehicle data integration
               </li>
               <li className="flex items-center">
                   <i className="fas fa-circle text-xs mr-2 text-primary"></i> Priority for emergency vehicles
               </li>
           </ul>
           <div className="feature-underline"></div>
          </div>
          {/* Feature Card 4 */}
          <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-300">
             <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
                 <i className="fas fa-chart-line text-2xl text-primary"></i>
             </div>
             <h3 className="text-primary text-xl font-bold mb-3">Real-Time Monitoring</h3>
             <p className="text-muted-foreground mb-4">
                 Comprehensive dashboard with live traffic data from sensors, cameras, and GPS sources.
             </p>
             <ul className="text-card-foreground text-sm space-y-2 mb-4">
                 <li className="flex items-center">
                     <i className="fas fa-circle text-xs mr-2 text-primary"></i> City-wide traffic visualization
                 </li>
                 <li className="flex items-center">
                     <i className="fas fa-circle text-xs mr-2 text-primary"></i> Incident detection alerts
                 </li>
             </ul>
             <div className="feature-underline"></div>
         </div>
         {/* Feature Card 5 */}
         <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-[400ms]">
             <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
                 <i className="fas fa-cloud text-2xl text-primary"></i>
             </div>
             <h3 className="text-primary text-xl font-bold mb-3">Cloud-Based Platform</h3>
             <p className="text-muted-foreground mb-4">
                 Scalable infrastructure that grows with your city&lsquo;s needs, accessible from anywhere.
             </p>
             <ul className="text-card-foreground text-sm space-y-2 mb-4">
                 <li className="flex items-center">
                     <i className="fas fa-circle text-xs mr-2 text-primary"></i> 99.99% uptime SLA
                 </li>
                 <li className="flex items-center">
                     <i className="fas fa-circle text-xs mr-2 text-primary"></i> Multi-city deployment support
                 </li>
             </ul>
             <div className="feature-underline"></div>
         </div>
         {/* Feature Card 6 */}
         <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition delay-500">
             <div className="w-16 h-16 border border-primary rounded-full flex items-center justify-center mb-6 float">
                 <i className="fas fa-shield-alt text-2xl text-primary"></i>
             </div>
             <h3 className="text-primary text-xl font-bold mb-3">Cybersecurity Focus</h3>
             <p className="text-muted-foreground mb-4">
                 Military-grade encryption protects critical infrastructure from digital threats.
             </p>
             <ul className="text-card-foreground text-sm space-y-2 mb-4">
                 <li className="flex items-center">
                     <i className="fas fa-circle text-xs mr-2 text-primary"></i> FIPS 140-2 compliant
                 </li>
                 <li className="flex items-center">
                     <i className="fas fa-circle text-xs mr-2 text-primary"></i> Continuous threat monitoring
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
