"use client";
// Filename: RouteOnePage.tsx (e.g., in app/landing/page.tsx or components/RouteOnePage.tsx)

import React, { useState, useEffect, useRef } from 'react';
import Head from 'next/head';

// Helper function for stat animation
const animateValue = (
    _element: HTMLElement | null, // Renamed to indicate it's not always used
    start: number,
    end: number,
    duration: number,
    suffix: string = '',
    setter?: React.Dispatch<React.SetStateAction<number>> // Specific type for number setters
) => {
    let startTimestamp: number | null = null;
    const step = (timestamp: number) => {
        if (startTimestamp === null) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);
        const currentValue = Math.floor(progress * (end - start) + start);
        if (setter) {
            setter(currentValue);
        } else if (_element) { // Only update innerHTML if _element is provided
            _element.innerHTML = currentValue + suffix;
        }

        if (progress < 1) {
            requestAnimationFrame(step);
        } else {
            if (setter) {
                setter(end);
            } else if (_element) { // Only update innerHTML if _element is provided
                _element.innerHTML = end + suffix;
            }
        }
    };
    requestAnimationFrame(step);
};


const RouteOnePage = () => {
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

    const matrixRainRef = useRef<HTMLDivElement | null>(null);
    const floatingOrbsRef = useRef<HTMLDivElement | null>(null);

    const scrollElementsRef = useRef<(HTMLElement | null)[]>([]);
    const addToScrollRefs = (el: HTMLElement | null) => {
        if (el instanceof HTMLElement && !scrollElementsRef.current.includes(el)) {
            scrollElementsRef.current.push(el);
        }
    };

    const [stat1, setStat1] = useState(0);
    const [stat2, setStat2] = useState(0);
    const [stat3, setStat3] = useState(0);
    const [stat4, setStat4] = useState(0);
    const statsSectionRef = useRef<HTMLElement | null>(null);
    const statsAnimatedRef = useRef<boolean>(false);

    const handleSmoothScroll = (
        e: React.MouseEvent<HTMLAnchorElement | HTMLButtonElement>, // Accept event from <a> or <button>
        targetId: string
    ) => {
        e.preventDefault();
        const targetElement = document.getElementById(targetId.substring(1));
        if (targetElement) {
            const header = document.querySelector('header');
            const headerHeight = header ? header.offsetHeight : 0;
            const targetPosition = targetElement.getBoundingClientRect().top + window.pageYOffset - headerHeight;

            window.scrollTo({ top: targetPosition, behavior: 'smooth' });
            if (mobileMenuOpen) setMobileMenuOpen(false);
        }
    };

    const handleDemoPlay = () => {
        alert('Demo simulation would launch here. In a production environment, this would open an interactive demo or video.');
    };

    // Background Effects (Matrix Rain & Floating Orbs)
    useEffect(() => {
        const matrixContainerForCleanup = matrixRainRef.current; // Capture for cleanup
        const orbsContainerForCleanup = floatingOrbsRef.current;   // Capture for cleanup

        const createMatrixRain = () => {
            const container = matrixRainRef.current; // Use current for setup
            if (!(container instanceof HTMLElement)) return;
            container.innerHTML = ''; // Clear existing rain on resize
            const chars = "0110101010100101001001010100101001010010100101001010101001011";
            const columns = Math.floor(window.innerWidth / 15);

            for (let i = 0; i < columns; i++) {
                const column = document.createElement('div');
                column.className = 'matrix-code'; // Styles from globals.css
                column.style.left = `${i * 15}px`;
                column.style.width = '15px';
                const delay = Math.random() * 5;
                const duration = 5 + Math.random() * 10;
                // Font size for matrix-code can be set in globals.css or overridden here if needed
                // column.style.fontSize = `${12 + Math.random() * 6}px`;
                column.style.animationDelay = `${delay}s`;
                column.style.animationDuration = `${duration}s`;
                const charCount = Math.floor(Math.random() * 15) + 5;
                let content = '';
                for (let j = 0; j < charCount; j++) {
                    const randomChar = chars.charAt(Math.floor(Math.random() * chars.length));
                    const opacity = 0.1 + Math.random() * 0.7;
                    content += `<span style="opacity:${opacity}">${randomChar}</span><br>`;
                }
                column.innerHTML = content;
                container.appendChild(column);
            }
        };

        const createFloatingOrbs = () => {
            const container = floatingOrbsRef.current; // Use current for setup
            if (!(container instanceof HTMLElement)) return;
            container.innerHTML = '';
            const orbCount = 12;
            for (let i = 0; i < orbCount; i++) {
                const orb = document.createElement('div');
                orb.className = 'absolute rounded-full pointer-events-none';
                const size = 2 + Math.random() * 6;
                const left = Math.random() * 100;
                const top = Math.random() * 100;
                const delay = Math.random() * 5;
                const duration = 10 + Math.random() * 20;
                // Use HSL from theme variables for orb color
                const color = `hsla(var(--matrix), ${0.05 + Math.random() * 0.15})`;
                orb.style.width = `${size}px`;
                orb.style.height = `${size}px`;
                orb.style.left = `${left}%`;
                orb.style.top = `${top}%`;
                orb.style.backgroundColor = color;
                orb.style.animation = `float ${duration}s ease-in-out infinite`;
                orb.style.animationDelay = `${delay}s`;
                container.appendChild(orb);
            }
        };

        createMatrixRain();
        createFloatingOrbs();
        const handleResize = () => {
            createMatrixRain();
            createFloatingOrbs();
        };
        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            if (matrixContainerForCleanup) matrixContainerForCleanup.innerHTML = '';
            if (orbsContainerForCleanup) orbsContainerForCleanup.innerHTML = '';
        };
    }, []); // Empty dependency array: runs once on mount, cleans up on unmount.

    // Scroll Animations & Stats Animation
    useEffect(() => {
        const observerCallback = (entries: IntersectionObserverEntry[]) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    if (entry.target === statsSectionRef.current && statsAnimatedRef.current === false) {
                        animateValue(null, 0, 42, 2000, '+', setStat1);
                        animateValue(null, 0, 35, 2000, '%', setStat2);
                        animateValue(null, 0, 28, 2000, '%', setStat3);
                        animateValue(null, 0, 55, 2000, '%', setStat4);
                        statsAnimatedRef.current = true;
                    }
                }
            });
        };

        const observer = new IntersectionObserver(observerCallback, { threshold: 0.1 });

        const elementsToObserve: HTMLElement[] = [];
        scrollElementsRef.current.forEach(el => {
            if (el instanceof HTMLElement) {
                elementsToObserve.push(el);
            }
        });
        // Add statsSectionRef to observation if it's an element and not already included
        if (statsSectionRef.current instanceof HTMLElement && !elementsToObserve.includes(statsSectionRef.current)) {
            elementsToObserve.push(statsSectionRef.current);
        }

        elementsToObserve.forEach(el => observer.observe(el));

        const handleScrollAnimation = () => {
            // Check visibility for elements in scrollElementsRef
            scrollElementsRef.current.forEach((el) => {
                if (el instanceof HTMLElement && el.getBoundingClientRect().top <= (window.innerHeight || document.documentElement.clientHeight)) {
                    if (!el.classList.contains('visible')) { // Only add if not already visible
                       el.classList.add('visible');
                    }
                }
            });
            // Check visibility for stats section
            if (
                statsSectionRef.current instanceof HTMLElement &&
                statsAnimatedRef.current === false &&
                statsSectionRef.current.getBoundingClientRect().top <= (window.innerHeight || document.documentElement.clientHeight)
            ) {
                if (!statsSectionRef.current.classList.contains('visible')) { // Also add visible to stats section
                    statsSectionRef.current.classList.add('visible');
                }
                animateValue(null, 0, 42, 2000, '+', setStat1);
                animateValue(null, 0, 35, 2000, '%', setStat2);
                animateValue(null, 0, 28, 2000, '%', setStat3);
                animateValue(null, 0, 55, 2000, '%', setStat4);
                statsAnimatedRef.current = true;
            }
        };

        handleScrollAnimation(); // Initial check on load
        window.addEventListener('scroll', handleScrollAnimation);

        // Capture the elements that were actually observed for cleanup
        const observedElementsForCleanup = [...elementsToObserve];

        return () => {
            observedElementsForCleanup.forEach(el => {
                 // No need to check instanceof HTMLElement here, as elementsToObserve only contains HTMLElements
                observer.unobserve(el);
            });
            window.removeEventListener('scroll', handleScrollAnimation);
        };
    }, [setStat1, setStat2, setStat3, setStat4]); // Dependencies for ESLint


    return (
        <>
            <Head>
                <title>Route One | Smart Traffic Management</title>
                <meta name="description" content="Route One harnesses AI and real-time data analytics for smart traffic management." />
                <meta charSet="UTF-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1.0" />
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" />
            </Head>

            {/* Base div inherits styles from body (bg-background, text-foreground, font-matrix) */}
            <div className="min-h-screen">
                {/* Animated Backgrounds: matrixRain and floatingOrbs are JS generated. */}
                <div id="matrixRain" ref={matrixRainRef} className="fixed inset-0 overflow-hidden pointer-events-none z-0"></div>
                <div id="floatingOrbs" ref={floatingOrbsRef} className="fixed inset-0 overflow-hidden pointer-events-none z-0"></div>
                <section className="hero-bg min-h-screen flex items-center pt-20 road-animation parallax-bg">
                    <div className="container mx-auto px-4 py-20">
                        <div className="flex flex-col md:flex-row hero-content">
                            <div ref={addToScrollRefs} className="hero-text w-full md:w-1/2 scroll-transition">
                                <h2 className="text-primary text-4xl md:text-6xl font-bold mb-6 matrix-glow">
                                    UNLOCK YOUR CITY&apos;S FLOW: <span className="text-foreground neon-text">AI-Powered Traffic Revolution</span>
                                </h2>
                                <p className="text-muted-foreground mb-8 text-lg">
                                    Route One harnesses the power of AI and real-time data analytics to optimize urban mobility, reduce congestion, and create smarter cities.
                                </p>
                                <div className="flex space-x-4">
                                    <button className="matrix-button hover:scale-105 transform">
                                        REQUEST DEMO <i className="fas fa-arrow-right ml-2"></i>
                                    </button>
                                    <button onClick={(e) => handleSmoothScroll(e, '#features')}
                                            className="matrix-button bg-secondary text-secondary-foreground hover:bg-accent hover:text-accent-foreground hover:scale-105 transform">
                                        EXPLORE TECHNOLOGY
                                    </button>
                                </div>
                            </div>
                            <div ref={addToScrollRefs} className="w-full md:w-1/2 flex justify-center mt-10 md:mt-0 scroll-transition">
                                <div className="relative w-80 h-80">
                                    <div className="absolute inset-0 border border-primary rounded-full pulse opacity-70"></div>
                                    <div className="absolute inset-4 border border-primary rounded-full pulse opacity-70" style={{animationDelay: '0.5s'}}></div>
                                    <div className="absolute inset-8 border border-primary rounded-full pulse opacity-70" style={{animationDelay: '1s'}}></div>
                                    <div className="absolute inset-12 flex items-center justify-center">
                                        <div className="w-40 h-40 bg-card/50 rounded-full flex items-center justify-center float relative border border-primary/30">
                                            <i className="fas fa-traffic-light text-6xl text-primary"></i>
                                            <div className="data-flow"> {/* .data-node styled in CSS with --primary */}
                                                <div className="data-node" style={{top: '20%', left: '20%', animationDelay: '0s'}}></div>
                                                <div className="data-node" style={{top: '30%', right: '20%', animationDelay: '0.5s'}}></div>
                                                <div className="data-node" style={{bottom: '20%', left: '30%', animationDelay: '1s'}}></div>
                                                <div className="data-node" style={{bottom: '30%', right: '30%', animationDelay: '1.5s'}}></div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <div className="section-divider"></div> {/* Styled in CSS with --primary */}

                {/* Features Section: Uses bg-background and .matrix-glow-card */}
                <section id="features" className="py-20 bg-background">
                    <div className="container mx-auto px-4">
                        <div ref={addToScrollRefs} className="text-center mb-16 scroll-transition">
                            <h2 className="text-primary text-3xl md:text-4xl font-bold mb-4 matrix-glow">SMART TRAFFIC SOLUTIONS</h2>
                            <p className="text-muted-foreground max-w-2xl mx-auto">
                                Our platform integrates cutting-edge technologies to deliver unparalleled traffic management capabilities.
                            </p>
                        </div>

                        <div className="feature-grid grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                            {/* Feature Card Example */}
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
                                <div className="feature-underline"></div> {/* Styled in CSS with primary color */}
                            </div>

                            <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition" style={{transitionDelay: '0.1s'}}>
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

                            <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition" style={{transitionDelay: '0.2s'}}>
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

                             <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition" style={{transitionDelay: '0.3s'}}>
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
                             <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition" style={{transitionDelay: '0.4s'}}>
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
                            <div ref={addToScrollRefs} className="matrix-glow-card p-8 scroll-transition" style={{transitionDelay: '0.5s'}}>
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

                <div className="section-divider"></div>

                {/* Solutions Section: .parallax-bg utility. Container uses theme border/bg. */}
                <section id="solutions" className="py-20 parallax-bg">
                    <div className="container mx-auto px-4">
                        <div ref={addToScrollRefs} className="border border-border bg-card/80 rounded-radius overflow-hidden scroll-transition">
                            <div className="grid grid-cols-1 lg:grid-cols-2">
                                {/* .smart-city-bg needs ::before stroke to use hsl(var(--matrix)) in globals.css */}
                                <div className="p-12 bg-card smart-city-bg">
                                    <h2 className="text-primary text-3xl font-bold mb-6 matrix-glow">YOUR CITY. SMARTER.</h2>
                                    <p className="text-muted-foreground mb-8">
                                        Route One transforms urban mobility with solutions tailored to your city&#39;s unique challenges.
                                    </p>
                                    <div className="space-y-6">
                                        {/* Solution Items use theme colors */}
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

                                {/* .process-bg needs ::before/::after gradients to use hsl(var(--primary)) / hsla(var(--matrix), opacity) in globals.css */}
                                <div className="bg-background/70 p-12 process-bg scanlines">
                                    <div ref={addToScrollRefs} className="scroll-transition" style={{transitionDelay: '0.2s'}}>
                                        <h3 className="text-primary text-2xl font-bold mb-6">IMPLEMENTATION PROCESS</h3>
                                        <div className="space-y-8 relative z-10">
                                            {/* Process Steps use theme colors */}
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

                <div className="section-divider"></div>

                {/* Demo Section: .road-animation utility. Container uses theme styles. */}
                <section id="demo" className="py-20 bg-background road-animation">
                    <div className="container mx-auto px-4">
                        <div ref={addToScrollRefs} className="text-center mb-16 scroll-transition">
                            <h2 className="text-primary text-3xl md:text-4xl font-bold mb-4 matrix-glow">EXPERIENCE ROUTE ONE</h2>
                            <p className="text-muted-foreground max-w-2xl mx-auto">
                                See how our platform can transform your city&lsquo;s traffic management.
                            </p>
                        </div>

                        <div ref={addToScrollRefs} className="border border-border bg-card rounded-radius overflow-hidden max-w-4xl mx-auto scroll-transition">
                            {/* .demo-visual ::before, .demo-grid, .demo-cell, .demo-graph, .demo-stat need their hsla/hsl colors set in globals.css */}
                            <div className="aspect-w-16 aspect-h-9 demo-visual map-grid"> {/* Used map-grid for thematic bg */}
                                <div className="w-full h-96 flex items-center justify-center relative">
                                    <div className="demo-grid">
                                        {[...Array(16)].map((_, i) => <div key={i} className="demo-cell"></div>)}
                                    </div>
                                    <div className="demo-graph">
                                        <div className="demo-graph-line"></div>
                                    </div>
                                    <div className="demo-stats">
                                        <div className="demo-stat">Traffic Flow: 87%</div>
                                        <div className="demo-stat">Incidents: 2</div>
                                        <div className="demo-stat">Signals Optimized: 42</div>
                                    </div>
                                    <div className="text-center relative z-10">
                                        <i className="fas fa-play-circle text-6xl text-primary mb-4 pulse cursor-pointer" onClick={handleDemoPlay}></i>
                                        <p className="text-primary">CLICK TO VIEW DEMO</p>
                                    </div>
                                </div>
                            </div>
                            <div className="p-6 bg-card/70 flex flex-wrap justify-between items-center border-t border-border">
                                <div>
                                    <h3 className="text-primary text-xl font-bold mb-2">CITY TRAFFIC CONTROL CENTER</h3>
                                    <p className="text-muted-foreground text-sm">Live data visualization and control interface</p>
                                        </div>
                                <button onClick={handleDemoPlay} className="matrix-button mt-4 sm:mt-0 hover:scale-105 transform">
                                    LAUNCH FULL DEMO <i className="fas fa-external-link-alt ml-2"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </section>

                <div className="section-divider"></div>

                {/* Stats Section: Uses theme bg and text colors. */}
                <section ref={statsSectionRef} className="py-20 bg-background/50">
                    <div className="container mx-auto px-4">
                        <div ref={addToScrollRefs} className="text-center mb-12 scroll-transition">
                            <h3 className="text-primary text-2xl font-bold mb-2 matrix-glow">ROUTE ONE BY THE NUMBERS</h3>
                            <p className="text-muted-foreground max-w-2xl mx-auto">
                                Quantifiable impact across our network of smart cities
                            </p>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
                            <div ref={addToScrollRefs} className="text-center scroll-transition">
                                <div className="text-primary text-4xl font-bold mb-2">{stat1}+</div>
                                <p className="text-muted-foreground">Cities Optimized</p>
                            </div>
                            <div ref={addToScrollRefs} className="text-center scroll-transition" style={{transitionDelay: '0.1s'}}>
                                <div className="text-primary text-4xl font-bold mb-2">{stat2}%</div>
                                <p className="text-muted-foreground">Avg. Traffic Reduction</p>
                            </div>
                            <div ref={addToScrollRefs} className="text-center scroll-transition" style={{transitionDelay: '0.2s'}}>
                                <div className="text-primary text-4xl font-bold mb-2">{stat3}%</div>
                                <p className="text-muted-foreground">Avg. Emission Decrease</p>
                            </div>
                            <div ref={addToScrollRefs} className="text-center scroll-transition" style={{transitionDelay: '0.3s'}}>
                                <div className="text-primary text-4xl font-bold mb-2">{stat4}%</div>
                                <p className="text-muted-foreground">Avg. Response Time Improvement</p>
                            </div>
                        </div>
                    </div>
                </section>

                <div className="section-divider"></div>

                {/* Contact Section: Container uses theme styles. Form elements use .matrix-input and .matrix-button. */}
                <section id="contact" className="py-20">
                    <div className="container mx-auto px-4">
                        <div ref={addToScrollRefs} className="border border-border bg-card/80 rounded-radius overflow-hidden scroll-transition">
                            <div className="grid grid-cols-1 lg:grid-cols-2">
                                <div className="p-12 bg-card">
                                    <h2 className="text-primary text-3xl font-bold mb-6 matrix-glow">GET IN TOUCH</h2>
                                    <p className="text-muted-foreground mb-8">
                                        Ready to transform your city&apos;s traffic management? Contact our team.
                                    </p>
                                    <div className="space-y-6">
                                        <div className="flex items-center">
                                            <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mr-4 flex-shrink-0 float">
                                                <i className="fas fa-map-marker-alt text-primary"></i>
                                            </div>
                                            <p className="text-muted-foreground">123 Cyber Lane, Neo City, NC 10101</p>
                                        </div>
                                        <div className="flex items-center">
                                            <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mr-4 flex-shrink-0 float">
                                                <i className="fas fa-phone text-primary"></i>
                                            </div>
                                            <p className="text-muted-foreground">+1 (555) 010-1010</p>
                                        </div>
                                        <div className="flex items-center">
                                            <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mr-4 flex-shrink-0 float">
                                                <i className="fas fa-envelope text-primary"></i>
                                            </div>
                                            <p className="text-muted-foreground">contact@routeone.tech</p>
                                        </div>
                                    </div>
                                    <div className="mt-8 flex space-x-4">
                                        <a href="#" className="border border-primary text-primary rounded-full w-10 h-10 flex items-center justify-center hover:bg-accent hover:text-accent-foreground transition hover:scale-110 transform">
                                            <i className="fab fa-twitter"></i>
                                        </a>
                                        <a href="#" className="border border-primary text-primary rounded-full w-10 h-10 flex items-center justify-center hover:bg-accent hover:text-accent-foreground transition hover:scale-110 transform">
                                            <i className="fab fa-linkedin-in"></i>
                                        </a>
                                        <a href="#" className="border border-primary text-primary rounded-full w-10 h-10 flex items-center justify-center hover:bg-accent hover:text-accent-foreground transition hover:scale-110 transform">
                                            <i className="fab fa-github"></i>
                                        </a>
                                    </div>
                                </div>
                                <div className="bg-background/70 p-12 scanlines"> {/* Added .scanlines for effect */}
                                    <form ref={addToScrollRefs} className="scroll-transition" style={{transitionDelay: '0.2s'}} onSubmit={(e) => e.preventDefault()}>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                                            <div>
                                                <label className="block text-primary text-sm font-bold mb-2" htmlFor="name">NAME</label>
                                                <input className="matrix-input w-full py-3 px-4" id="name" type="text" placeholder="Your Full Name" />
                                            </div>
                                            <div>
                                                <label className="block text-primary text-sm font-bold mb-2" htmlFor="email">EMAIL</label>
                                                <input className="matrix-input w-full py-3 px-4" id="email" type="email" placeholder="your.email@example.com" />
                                            </div>
                                        </div>
                                        <div className="mb-6">
                                            <label className="block text-primary text-sm font-bold mb-2" htmlFor="subject">SUBJECT</label>
                                            <input className="matrix-input w-full py-3 px-4" id="subject" type="text" placeholder="e.g., Demo Request for Neo City" />
                                        </div>
                                        <div className="mb-6">
                                            <label className="block text-primary text-sm font-bold mb-2" htmlFor="message">MESSAGE</label>
                                            <textarea className="matrix-input w-full py-3 px-4" id="message" rows={4} placeholder="Tell us about your city's traffic challenges"></textarea>
                                        </div>
                                        <button type="submit" className="matrix-button w-full hover:scale-105 transform">
                                            SEND MESSAGE <i className="fas fa-paper-plane ml-2"></i>
                                        </button>
                                    </form>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                {/* Footer: Uses theme styles and .road-animation utility. */}
                <footer className="bg-card py-12 road-animation border-t border-border">
                    <div className="container mx-auto px-4">
                        <div className="flex flex-col md:flex-row justify-between items-center">
                            <div className="flex items-center mb-6 md:mb-0">
                                <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mr-3 float">
                                    <i className="fas fa-route text-xl text-primary"></i>
                                </div>
                                <h1 className="text-foreground text-2xl font-bold">ROUTE ONE</h1>
                            </div>
                            <div className="text-center md:text-right">
                                <p className="text-muted-foreground mb-2">© {new Date().getFullYear()} Route One Technologies. All rights reserved.</p>
                                <div className="flex justify-center md:justify-end space-x-6">
                                    <a href="#" className="text-muted-foreground hover:text-primary transition">Privacy</a>
                                    <a href="#" className="text-muted-foreground hover:text-primary transition">Terms</a>
                                    <a href="#" className="text-muted-foreground hover:text-primary transition">Security</a>
                                </div>
                            </div>
                        </div>
                    </div>
                </footer>
            </div>
        </>
    );
};

export default RouteOnePage;