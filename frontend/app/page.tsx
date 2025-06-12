"use client";
// Filename: RouteOnePage.tsx (e.g., in app/landing/page.tsx or components/RouteOnePage.tsx)

import React, { useState, useEffect, useRef } from 'react';
import Head from 'next/head';
import FeaturesSection from '@/components/landing/FeaturesSection';
import SolutionsSection from '@/components/landing/SolutionsSection';
import {
  ArrowRight, TrafficCone, PlayCircle, ExternalLink, MapPin, Phone, Mail,
  Twitter, Linkedin, Github, Send, Route as RouteIcon
} from 'lucide-react'; // Route aliased to RouteIcon

// Helper function for stat animation
const animateValue = (
    _element: HTMLElement | null, // Renamed to indicate it's not always used
    start: number,
    end: number,
    duration: number,
    suffix: string = '',
    setter?: React.Dispatch<React.SetStateAction<number>> // Specific type for number setters
) => {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (prefersReducedMotion) {
        if (setter) {
            setter(end);
        } else if (_element) {
            _element.innerHTML = end + suffix;
        }
        return;
    }

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
            if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                // Optionally, provide a scaled-down version or clear if previously rendered
                if (matrixRainRef.current) matrixRainRef.current.innerHTML = '';
                return;
            }
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
            if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                // Optionally, provide a scaled-down version or clear if previously rendered
                if (floatingOrbsRef.current) floatingOrbsRef.current.innerHTML = '';
                return;
            }
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
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        const observerCallback = (entries: IntersectionObserverEntry[]) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    if (entry.target === statsSectionRef.current && statsAnimatedRef.current === false) {
                        if (prefersReducedMotion) {
                            setStat1(42);
                            setStat2(35);
                            setStat3(28);
                            setStat4(55);
                        } else {
                            animateValue(null, 0, 42, 2000, '+', setStat1);
                            animateValue(null, 0, 35, 2000, '%', setStat2);
                            animateValue(null, 0, 28, 2000, '%', setStat3);
                            animateValue(null, 0, 55, 2000, '%', setStat4);
                        }
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
                if (prefersReducedMotion) {
                    setStat1(42);
                    setStat2(35);
                    setStat3(28);
                    setStat4(55);
                } else {
                    animateValue(null, 0, 42, 2000, '+', setStat1);
                    animateValue(null, 0, 35, 2000, '%', setStat2);
                    animateValue(null, 0, 28, 2000, '%', setStat3);
                    animateValue(null, 0, 55, 2000, '%', setStat4);
                }
                statsAnimatedRef.current = true;
            }
        };

        // Initial check on load
        // If reduced motion is preferred, set final stat values directly if section is visible
        if (prefersReducedMotion && statsSectionRef.current && statsAnimatedRef.current === false &&
            statsSectionRef.current.getBoundingClientRect().top <= (window.innerHeight || document.documentElement.clientHeight)) {
            statsSectionRef.current.classList.add('visible'); // Ensure section is marked visible
            setStat1(42);
            setStat2(35);
            setStat3(28);
            setStat4(55);
            statsAnimatedRef.current = true;
        } else if (!prefersReducedMotion) { // Only run animation if not reduced motion
             handleScrollAnimation();
        } else { // Reduced motion but section not yet visible, still mark elements visible if they are
            scrollElementsRef.current.forEach((el) => {
                if (el instanceof HTMLElement && el.getBoundingClientRect().top <= (window.innerHeight || document.documentElement.clientHeight)) {
                    if (!el.classList.contains('visible')) {
                       el.classList.add('visible');
                    }
                }
            });
            if (statsSectionRef.current && statsSectionRef.current.getBoundingClientRect().top <= (window.innerHeight || document.documentElement.clientHeight)) {
                if (!statsSectionRef.current.classList.contains('visible')) {
                    statsSectionRef.current.classList.add('visible');
                }
            }
        }


        if (!prefersReducedMotion) { // Only add scroll listener if animations are active
            window.addEventListener('scroll', handleScrollAnimation);
        }

        // Capture the elements that were actually observed for cleanup
        const observedElementsForCleanup = [...elementsToObserve];

        return () => {
            observedElementsForCleanup.forEach(el => {
                observer.unobserve(el);
            });
            if (!prefersReducedMotion) {
                window.removeEventListener('scroll', handleScrollAnimation);
            }
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
                                <h2 className="text-primary text-4xl md:text-6xl font-bold mb-6 matrix-glow tracking-normal"> {/* Added tracking-normal */}
                                    UNLOCK YOUR CITY&apos;S FLOW: <span className="text-primary-foreground">AI-Powered Traffic Revolution</span> {/* Changed to solid green, removed neon-text */}
                                </h2>
                                <p className="text-muted-foreground mb-8 text-lg tracking-normal"> {/* Added tracking-normal */}
                                    Route One harnesses the power of AI and real-time data analytics to optimize urban mobility, reduce congestion, and create smarter cities.
                                </p>
                                <div className="flex space-x-4">
                                    <button className="matrix-button hover:scale-105 transform">
                                        REQUEST DEMO <ArrowRight className="ml-2 h-4 w-4 text-primary-foreground" />
                                    </button>
                                    <button onClick={(e) => handleSmoothScroll(e, '#features')}
                                            className="matrix-button bg-secondary text-secondary-foreground hover:bg-primary/90 hover:scale-105 transform"> {/* Changed hover */}
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
                                            <TrafficCone size={64} className="text-primary" /> {/* Replaced fa-traffic-light, size is approx */}
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

                <FeaturesSection addToScrollRefs={addToScrollRefs} />

                <div className="section-divider"></div>

                <SolutionsSection addToScrollRefs={addToScrollRefs} />

                <div className="section-divider"></div>

                {/* Demo Section: .road-animation utility. Container uses theme styles. */}
                <section id="demo" className="py-20 bg-background road-animation">
                    <div className="container mx-auto px-4">
                        <div ref={addToScrollRefs} className="text-center mb-16 scroll-transition">
                            <h2 className="text-primary text-3xl md:text-4xl font-bold mb-4 matrix-glow tracking-normal">EXPERIENCE ROUTE ONE</h2> {/* Added tracking-normal */}
                            <p className="text-muted-foreground max-w-2xl mx-auto tracking-normal"> {/* Added tracking-normal */}
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
                                        {/* Assuming demo-stat text should also be tracking-normal, but it's a very specific style */}
                                        <div className="demo-stat tracking-normal">Traffic Flow: 87%</div>
                                        <div className="demo-stat tracking-normal">Incidents: 2</div>
                                        <div className="demo-stat tracking-normal">Signals Optimized: 42</div>
                                    </div>
                                    <div className="text-center relative z-10">
                                        <PlayCircle size={64} className="text-primary mb-4 pulse cursor-pointer" onClick={handleDemoPlay} /> {/* Replaced fa-play-circle */}
                                        <p className="text-primary tracking-normal">CLICK TO VIEW DEMO</p> {/* Added tracking-normal */}
                                    </div>
                                </div>
                            </div>
                            <div className="p-6 bg-card/70 flex flex-wrap justify-between items-center border-t border-border">
                                <div>
                                    <h3 className="text-primary text-xl font-bold mb-2 tracking-normal">CITY TRAFFIC CONTROL CENTER</h3> {/* Added tracking-normal */}
                                    <p className="text-muted-foreground text-sm tracking-normal">Live data visualization and control interface</p> {/* Added tracking-normal */}
                                        </div>
                                <button onClick={handleDemoPlay} className="matrix-button mt-4 sm:mt-0 hover:scale-105 transform">
                                    LAUNCH FULL DEMO <ExternalLink className="ml-2 h-4 w-4 text-primary-foreground" />
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
                            <h3 className="text-primary text-2xl font-bold mb-2 matrix-glow tracking-normal">ROUTE ONE BY THE NUMBERS</h3> {/* Added tracking-normal */}
                            <p className="text-muted-foreground max-w-2xl mx-auto tracking-normal"> {/* Added tracking-normal */}
                                Quantifiable impact across our network of smart cities
                            </p>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
                            <div ref={addToScrollRefs} className="text-center scroll-transition">
                                <div className="text-primary text-4xl font-bold mb-2 tracking-normal">{stat1}+</div> {/* Added tracking-normal to stat number */}
                                <p className="text-muted-foreground tracking-normal">Cities Optimized</p> {/* Added tracking-normal */}
                            </div>
                            <div ref={addToScrollRefs} className="text-center scroll-transition" style={{transitionDelay: '0.1s'}}>
                                <div className="text-primary text-4xl font-bold mb-2 tracking-normal">{stat2}%</div> {/* Added tracking-normal to stat number */}
                                <p className="text-muted-foreground tracking-normal">Avg. Traffic Reduction</p> {/* Added tracking-normal */}
                            </div>
                            <div ref={addToScrollRefs} className="text-center scroll-transition" style={{transitionDelay: '0.2s'}}>
                                <div className="text-primary text-4xl font-bold mb-2 tracking-normal">{stat3}%</div> {/* Added tracking-normal to stat number */}
                                <p className="text-muted-foreground tracking-normal">Avg. Emission Decrease</p> {/* Added tracking-normal */}
                            </div>
                            <div ref={addToScrollRefs} className="text-center scroll-transition" style={{transitionDelay: '0.3s'}}>
                                <div className="text-primary text-4xl font-bold mb-2 tracking-normal">{stat4}%</div> {/* Added tracking-normal to stat number */}
                                <p className="text-muted-foreground tracking-normal">Avg. Response Time Improvement</p> {/* Added tracking-normal */}
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
                                                <MapPin className="h-5 w-5 text-primary" /> {/* Replaced fa-map-marker-alt */}
                                            </div>
                                <p className="text-muted-foreground tracking-normal">123 Cyber Lane, Neo City, NC 10101</p> {/* Added tracking-normal */}
                                        </div>
                                        <div className="flex items-center">
                                            <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mr-4 flex-shrink-0 float">
                                                <Phone className="h-5 w-5 text-primary" /> {/* Replaced fa-phone */}
                                            </div>
                                <p className="text-muted-foreground tracking-normal">+1 (555) 010-1010</p> {/* Added tracking-normal */}
                                        </div>
                                        <div className="flex items-center">
                                            <div className="border border-primary rounded-full w-10 h-10 flex items-center justify-center mr-4 flex-shrink-0 float">
                                                <Mail className="h-5 w-5 text-primary" /> {/* Replaced fa-envelope */}
                                            </div>
                                <p className="text-muted-foreground tracking-normal">contact@routeone.tech</p> {/* Added tracking-normal */}
                                        </div>
                                    </div>
                                    <div className="mt-8 flex space-x-4">
                                        <a href="#" className="border border-primary text-primary rounded-full w-10 h-10 flex items-center justify-center hover:bg-primary hover:text-primary-foreground transition hover:scale-110 transform"> {/* Updated hover for social icons */}
                                            <Twitter className="h-5 w-5" /> {/* Replaced fab fa-twitter, color from parent <a> */}
                                        </a>
                                        <a href="#" className="border border-primary text-primary rounded-full w-10 h-10 flex items-center justify-center hover:bg-primary hover:text-primary-foreground transition hover:scale-110 transform"> {/* Updated hover for social icons */}
                                            <Linkedin className="h-5 w-5" /> {/* Replaced fab fa-linkedin-in, color from parent <a> */}
                                        </a>
                                        <a href="#" className="border border-primary text-primary rounded-full w-10 h-10 flex items-center justify-center hover:bg-primary hover:text-primary-foreground transition hover:scale-110 transform"> {/* Updated hover for social icons */}
                                            <Github className="h-5 w-5" /> {/* Replaced fab fa-github, color from parent <a> */}
                                        </a>
                                    </div>
                                </div>
                                <div className="bg-background/70 p-12 scanlines"> {/* Added .scanlines for effect */}
                                    <form ref={addToScrollRefs} className="scroll-transition" style={{transitionDelay: '0.2s'}} onSubmit={(e) => e.preventDefault()}>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                                            <div>
                                                <label className="block text-primary text-sm font-bold mb-2 tracking-normal" htmlFor="name">NAME</label> {/* Added tracking-normal */}
                                                <input className="matrix-input w-full py-3 px-4 tracking-normal" id="name" type="text" placeholder="Your Full Name" /> {/* Added tracking-normal */}
                                            </div>
                                            <div>
                                                <label className="block text-primary text-sm font-bold mb-2 tracking-normal" htmlFor="email">EMAIL</label> {/* Added tracking-normal */}
                                                <input className="matrix-input w-full py-3 px-4 tracking-normal" id="email" type="email" placeholder="your.email@example.com" /> {/* Added tracking-normal */}
                                            </div>
                                        </div>
                                        <div className="mb-6">
                                            <label className="block text-primary text-sm font-bold mb-2 tracking-normal" htmlFor="subject">SUBJECT</label> {/* Added tracking-normal */}
                                            <input className="matrix-input w-full py-3 px-4 tracking-normal" id="subject" type="text" placeholder="e.g., Demo Request for Neo City" /> {/* Added tracking-normal */}
                                        </div>
                                        <div className="mb-6">
                                            <label className="block text-primary text-sm font-bold mb-2 tracking-normal" htmlFor="message">MESSAGE</label> {/* Added tracking-normal */}
                                            <textarea className="matrix-input w-full py-3 px-4 tracking-normal" id="message" rows={4} placeholder="Tell us about your city's traffic challenges"></textarea> {/* Added tracking-normal */}
                                        </div>
                                        <button type="submit" className="matrix-button w-full hover:scale-105 transform">
                                            SEND MESSAGE <Send className="ml-2 h-4 w-4 text-primary-foreground" /> {/* Replaced fa-paper-plane */}
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
                                    <RouteIcon size={24} className="text-primary" /> {/* Replaced fa-route */}
                                </div>
                                <h1 className="text-foreground text-2xl font-bold tracking-normal">ROUTE ONE</h1> {/* Added tracking-normal */}
                            </div>
                            <div className="text-center md:text-right">
                                <p className="text-muted-foreground mb-2 tracking-normal">© {new Date().getFullYear()} Route One Technologies. All rights reserved.</p> {/* Added tracking-normal */}
                                <div className="flex justify-center md:justify-end space-x-6">
                                    <a href="#" className="text-muted-foreground hover:text-primary transition tracking-normal">Privacy</a> {/* Added tracking-normal */}
                                    <a href="#" className="text-muted-foreground hover:text-primary transition tracking-normal">Terms</a> {/* Added tracking-normal */}
                                    <a href="#" className="text-muted-foreground hover:text-primary transition tracking-normal">Security</a> {/* Added tracking-normal */}
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