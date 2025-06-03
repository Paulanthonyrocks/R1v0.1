// frontend/components/landing/SolutionsSection.tsx
import React from 'react';

interface SolutionsSectionProps {
  addToScrollRefs: (el: HTMLElement | null) => void;
}

const SolutionsSection: React.FC<SolutionsSectionProps> = ({ addToScrollRefs }) => {
  return (
    <section id="solutions" className="py-20 parallax-bg">
      <div className="container mx-auto px-4">
        <div ref={addToScrollRefs} className="border border-border bg-card/80 rounded-radius overflow-hidden scroll-transition">
          <div className="grid grid-cols-1 lg:grid-cols-2">
            <div className="p-12 bg-card smart-city-bg">
              <h2 className="text-primary text-3xl font-bold mb-6 matrix-glow">YOUR CITY. SMARTER.</h2>
              <p className="text-muted-foreground mb-8">
                Route One transforms urban mobility with solutions tailored to your city&#39;s unique challenges.
              </p>
              <div className="space-y-6">
                {/* Solution Item 1 */}
                <div className="flex items-start">
                  <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                    <i className="fas fa-check text-primary"></i>
                  </div>
                  <div>
                    <h4 className="text-primary font-bold mb-1">Congestion Reduction</h4>
                    <p className="text-muted-foreground text-sm">
                      Decrease traffic jams by up to 40% with our predictive algorithms.
                    </p>
                  </div>
                </div>
                {/* Solution Item 2 */}
                <div className="flex items-start">
                   <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                       <i className="fas fa-check text-primary"></i>
                   </div>
                   <div>
                       <h4 className="text-primary font-bold mb-1">Emission Control</h4>
                       <p className="text-muted-foreground text-sm">
                           Reduce vehicle idle time and lower carbon emissions significantly.
                       </p>
                   </div>
               </div>
               {/* Solution Item 3 */}
               <div className="flex items-start">
                   <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                       <i className="fas fa-check text-primary"></i>
                   </div>
                   <div>
                       <h4 className="text-primary font-bold mb-1">Emergency Priority</h4>
                       <p className="text-muted-foreground text-sm">
                           Clear paths automatically for first responders when seconds count.
                       </p>
                   </div>
               </div>
              </div>
            </div>

            <div className="bg-background/70 p-12 process-bg scanlines">
              <div ref={addToScrollRefs} className="scroll-transition" style={{transitionDelay: '0.2s'}}>
                <h3 className="text-primary text-2xl font-bold mb-6">IMPLEMENTATION PROCESS</h3>
                <div className="space-y-8 relative z-10">
                  {/* Process Step 1 */}
                  <div className="flex items-start">
                      <div className="border border-primary rounded-full w-12 h-12 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                          <span className="text-primary">1</span>
                      </div>
                      <div>
                          <h4 className="text-primary font-bold mb-1">Assessment</h4>
                          <p className="text-muted-foreground">
                          Comprehensive analysis of your current traffic infrastructure.
                          </p>
                      </div>
                  </div>
                  {/* Process Step 2 */}
                  <div className="flex items-start">
                      <div className="border border-primary rounded-full w-12 h-12 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                          <span className="text-primary">2</span>
                      </div>
                      <div>
                          <h4 className="text-primary font-bold mb-1">Customization</h4>
                          <p className="text-muted-foreground">
                              Tailored solution design for your city&apos;s specific needs.
                          </p>
                      </div>
                  </div>
                  {/* Process Step 3 */}
                  <div className="flex items-start">
                      <div className="border border-primary rounded-full w-12 h-12 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                          <span className="text-primary">3</span>
                      </div>
                      <div>
                          <h4 className="text-primary font-bold mb-1">Deployment</h4>
                          <p className="text-muted-foreground">
                              Seamless integration with minimal disruption to existing systems.
                          </p>
                      </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default SolutionsSection;
