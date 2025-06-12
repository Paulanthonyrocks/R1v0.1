// frontend/components/landing/SolutionsSection.tsx
import React from 'react';
import { Check } from 'lucide-react';

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
              <h2 className="text-primary text-3xl font-bold mb-6 matrix-glow tracking-normal">YOUR CITY. SMARTER.</h2> {/* Added tracking-normal */}
              <p className="text-muted-foreground mb-8 tracking-normal"> {/* Added tracking-normal */}
                Route One transforms urban mobility with solutions tailored to your city&#39;s unique challenges.
              </p>
              <div className="space-y-6">
                {/* Solution Item 1 */}
                <div className="flex items-start">
                  <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                    <Check className="h-5 w-5 text-primary" /> {/* Replaced fa-check */}
                  </div>
                  <div>
                    <h4 className="text-primary font-bold mb-1 tracking-normal">Congestion Reduction</h4> {/* Added tracking-normal */}
                    <p className="text-muted-foreground text-sm tracking-normal"> {/* Added tracking-normal */}
                      Decrease traffic jams by up to 40% with our predictive algorithms.
                    </p>
                  </div>
                </div>
                {/* Solution Item 2 */}
                <div className="flex items-start">
                   <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                       <Check className="h-5 w-5 text-primary" /> {/* Replaced fa-check */}
                   </div>
                   <div>
                       <h4 className="text-primary font-bold mb-1 tracking-normal">Emission Control</h4> {/* Added tracking-normal */}
                       <p className="text-muted-foreground text-sm tracking-normal"> {/* Added tracking-normal */}
                           Reduce vehicle idle time and lower carbon emissions significantly.
                       </p>
                   </div>
               </div>
               {/* Solution Item 3 */}
               <div className="flex items-start">
                   <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                       <Check className="h-5 w-5 text-primary" /> {/* Replaced fa-check */}
                   </div>
                   <div>
                       <h4 className="text-primary font-bold mb-1 tracking-normal">Emergency Priority</h4> {/* Added tracking-normal */}
                       <p className="text-muted-foreground text-sm tracking-normal"> {/* Added tracking-normal */}
                           Clear paths automatically for first responders when seconds count.
                       </p>
                   </div>
               </div>
              </div>
            </div>

            <div className="bg-background/70 p-12 process-bg scanlines">
              <div ref={addToScrollRefs} className="scroll-transition" style={{transitionDelay: '0.2s'}}>
                <h3 className="text-primary text-2xl font-bold mb-6 tracking-normal">IMPLEMENTATION PROCESS</h3> {/* Added tracking-normal */}
                <div className="space-y-8 relative z-10">
                  {/* Process Step 1 */}
                  <div className="flex items-start">
                      <div className="border border-primary rounded-full w-12 h-12 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                          <span className="text-primary tracking-normal">1</span> {/* Added tracking-normal */}
                      </div>
                      <div>
                          <h4 className="text-primary font-bold mb-1 tracking-normal">Assessment</h4> {/* Added tracking-normal */}
                          <p className="text-muted-foreground tracking-normal"> {/* Added tracking-normal */}
                          Comprehensive analysis of your current traffic infrastructure.
                          </p>
                      </div>
                  </div>
                  {/* Process Step 2 */}
                  <div className="flex items-start">
                      <div className="border border-primary rounded-full w-12 h-12 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                          <span className="text-primary tracking-normal">2</span> {/* Added tracking-normal */}
                      </div>
                      <div>
                          <h4 className="text-primary font-bold mb-1 tracking-normal">Customization</h4> {/* Added tracking-normal */}
                          <p className="text-muted-foreground tracking-normal"> {/* Added tracking-normal */}
                              Tailored solution design for your city&apos;s specific needs.
                          </p>
                      </div>
                  </div>
                  {/* Process Step 3 */}
                  <div className="flex items-start">
                      <div className="border border-primary rounded-full w-12 h-12 flex items-center justify-center mt-1 mr-4 flex-shrink-0 float">
                          <span className="text-primary tracking-normal">3</span> {/* Added tracking-normal */}
                      </div>
                      <div>
                          <h4 className="text-primary font-bold mb-1 tracking-normal">Deployment</h4> {/* Added tracking-normal */}
                          <p className="text-muted-foreground tracking-normal"> {/* Added tracking-normal */}
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
