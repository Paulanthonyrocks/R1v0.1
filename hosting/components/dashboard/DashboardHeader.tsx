File unchanged since last read. The content from the earlier read_file result in this conversation is still current — refer to that instead of re-reading.
            {/* MAIN HEADER BAR */}
            <div className="text-lcd-green flex items-center justify-between px-4 py-3 border-b border-lcd-green/40 relative font-mono">
                
                {/* LOGO SECTION: Refined Tactical Module */}
                <div className="flex items-center gap-4 z-10">
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <div 
                                id="mobile-menu-trigger"
                                className="group relative flex items-center gap-3 px-4 py-2 border border-lcd-green/40 bg-lcd-green/5 hover:bg-lcd-green hover:text-industrial-bg transition-all duration-200 cursor-pointer"
                            >
                                {/* Tactical Corner Brackets */}
                                <div className="absolute -top-px -left-px w-1.5 h-1.5 border-t-2 border-l-2 border-lcd-green group-hover:border-industrial-bg" />
                                <div className="absolute -top-px -right-px w-1.5 h-1.5 border-t-2 border-r-2 border-lcd-green group-hover:border-industrial-bg" />
                                <div className="absolute -bottom-px -left-px w-1.5 h-1.5 border-b-2 border-l-2 border-lcd-green group-hover:border-industrial-bg" />
                                <div className="absolute -bottom-px -right-px w-1.5 h-1.5 border-b-2 border-r-2 border-lcd-green group-hover:border-industrial-bg" />
                                
                                <div className="relative flex items-center gap-3">
                                    <Signal size={18} className="group-hover:animate-pulse" />
                                    <div className="flex flex-col leading-none">
                                        <span className="text-sm font-black tracking-tighter uppercase">Traffic Hub</span>
                                        <span className="text-[8px] opacity-50 uppercase tracking-widest font-bold">S-SYNC v1.0.4</span>
                                    </div>
                                </div>
                            </div>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="matrix-card min-w-[220px] mt-2 border-lcd-green bg-industrial-panel text-lcd-green">
                            {allNavItems.map((item) => (
                                <DropdownMenuItem key={item.href} asChild>
                                    <Link href={item.href} className={cn("w-full tracking-wider font-lcd py-2 px-4 uppercase text-sm transition-colors", pathname === item.href && "bg-lcd-green text-industrial-bg")}>
                                        {item.label}
                                    </Link>
                                </DropdownMenuItem>
                            ))}
                            <div className="h-px bg-lcd-green/20 my-1" />
                            <DropdownMenuItem asChild>
                                <Link href="/preferences" className="w-full tracking-wider font-lcd py-2 px-4 uppercase text-sm">PREFERENCES</Link>
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
                
                {/* NAVIGATION: Simplified and Spaced */}
                <nav className="hidden xl:flex items-center h-full gap-2 z-10">
                    {primaryNavItems.map((item) => (
                        <Link 
                            key={item.href} 
                            href={item.href} 
                            className={cn(
                                "flex items-center gap-2 px-4 py-1 text-[10px] font-bold tracking-widest uppercase transition-all relative group",
                                pathname === item.href 
                                    ? "bg-lcd-green text-industrial-bg" 
                                    : "text-lcd-green/50 hover:text-lcd-green hover:bg-lcd-green/10"
                            )}
                        >
                            <item.icon size={12} className={cn("transition-transform group-hover:scale-110", pathname === item.href && "text-industrial-bg")} /> 
                            <span>{item.label}</span>
                        </Link>
                    ))}
                </nav>

                {/* STATUS POD: Cleaned Frame */}
                <div className="flex items-center gap-4 z-10">
                    <div className="relative group hidden lg:flex items-center gap-6 px-4 py-1.5 border border-lcd-green/30 bg-black/40 font-mono text-[9px]">
                        <div className="absolute -top-px -left-px w-1.5 h-1.5 border-t-2 border-l-2 border-lcd-green/30" />
                        <div className="absolute -top-px -right-px w-1.5 h-1.5 border-t-2 border-r-2 border-lcd-green/30" />
                        <div className="absolute -bottom-px -left-px w-1.5 h-1.5 border-b-2 border-l-2 border-lcd-green/30" />
                        <div className="absolute -bottom-px -right-px w-1.5 h-1.5 border-b-2 border-r-2 border-lcd-green/30" />

                        <div className="flex items-center gap-2" title={isConnected ? "WebSocket Connected" : "WebSocket Disconnected"}>
                            <div className={cn("w-1.5 h-1.5", isConnected ? "bg-emerald-500 animate-pulse shadow-[0_0_5px_rgba(16,185,129,1)]" : "bg-red-500 shadow-[0_0_5px_rgba(239,68,68,1)]")}></div>
                            <span className="font-bold uppercase tracking-tighter opacity-80">Uplink: {isConnected ? 'OK' : 'Offline'}</span>
                        </div>
                        <div className="h-3 w-px bg-lcd-green/30" />
                        <div className="flex items-center gap-4 opacity-60">
                            <div className="flex flex-col leading-none">
                                <span className="text-[7px] opacity-50 uppercase">Coord</span>
                                <span className="font-bold">{coords.x}, {coords.y}</span>
                            </div>
                            <div className="flex flex-col leading-none text-right">
                                <span className="text-[7px] opacity-50 uppercase">Epoch</span>
                                <span className="font-bold text-lcd-green">{time}</span>
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center justify-center w-8 h-8 border border-lcd-green/40 bg-lcd-green/5 hover:bg-lcd-green hover:text-industrial-bg transition-all cursor-pointer group">
                        <BatteryFull size={14} className="opacity-70 group-hover:opacity-100" />
                    </div>
                </div>
            </div>