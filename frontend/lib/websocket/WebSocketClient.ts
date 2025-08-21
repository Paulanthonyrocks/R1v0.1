import { TokenManager } from '../auth/TokenManager';
import { errorNotifier } from '../utils/errorNotifier';

interface WebSocketErrorEvent extends Event {
    message?: string;
}

// Utility function to get or create a client ID
const getOrCreateClientId = () => {
    if (typeof window === 'undefined') return '';
    let clientId = localStorage.getItem('ws_client_id');
    if (!clientId) {
        clientId = crypto.randomUUID();
        localStorage.setItem('ws_client_id', clientId);
    }
    return clientId;
};

// Make the listener type generic
type MessageListener<T> = (data: T) => void;

// Type definitions for specific message payloads
export type MetricValue = string | number | boolean | null;

export interface RealtimeMetricsUpdate {
    feed_id: string;
    timestamp: string;
    metrics: Record<string, MetricValue>;
}

export interface GlobalRealtimeMetrics {
    timestamp: string;
    metrics_source?: string;
    congestion_index?: number;
    average_speed_kmh?: number;
    active_incidents_count?: number;
    total_flow?: number;
    feed_statuses?: Record<string, number>;
    custom_metrics?: Record<string, MetricValue>;
}

export interface GeneralNotification {
    message_type: string;
    title?: string;
    message: string;
    severity: 'info' | 'warning' | 'error';
    suggested_actions?: string[];
    timestamp: string;
}

export interface ErrorNotification {
    error_code?: string;
    message: string;
    details?: string;
    timestamp: string;
}

// Enum for valid message types expected by the server
export enum WebSocketMessageType {
    METRICS_UPDATE = 'metrics_update',
    GLOBAL_REALTIME_METRICS_UPDATE = 'global_realtime_metrics_update',
    NEW_ALERT = 'new_alert',
    SIGNAL_UPDATE = 'signal_update',
    VIDEO_FRAME = 'video_frame',
    FEED_STATUS_UPDATE = 'feed_status_update',
    GENERAL_NOTIFICATION = 'general_notification',
    ERROR_NOTIFICATION = 'error_notification',
    PREDICTION_ALERT = 'prediction_alert',
    ALERT_STATUS_UPDATE = 'alert_status_update',
    NODE_CONGESTION_UPDATE = 'node_congestion_update',
    USER_SPECIFIC_ALERT = 'user_specific_alert',
    INITIAL_FEED_STATUSES = 'initial_feed_statuses',
    PONG = 'pong',
    AUTH_SUCCESS = 'auth_success',
    AUTH_FAILURE = 'auth_failure',
    AUTHENTICATE = 'authenticate',
    INTERNAL_PING = '__internal_ping',
    PING = 'ping',
    SUBSCRIBE_TO_FEED = 'subscribe_to_feed',
    GET_INITIAL_FEED_STATUSES = 'get_initial_feed_statuses'
}

export interface WebSocketMessage<T = unknown> {
    type: WebSocketMessageType;
    data?: T | null;
    client_id?: string;
    correlation_id?: string;
    timestamp?: number;
}

export interface IWebSocketClient {
    connect(token: string): Promise<void>;
    disconnect(): void;
    send<T>(data: WebSocketMessage<T>): void;
    subscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>): void;
    unsubscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>): void;
    reconnectWithNewToken(token: string): Promise<void>;
}

enum ConnectionState {
    DISCONNECTED = 'disconnected',
    CONNECTING = 'connecting',
    CONNECTED = 'connected',
    RECONNECTING = 'reconnecting',
    ERROR = 'error'
}

export class WebSocketClient implements IWebSocketClient {
    private listeners: Map<WebSocketMessageType, Set<MessageListener<unknown>>> = new Map();
    private ws: WebSocket | null = null;
    private reconnectAttempts = 0;
    private maxReconnectAttempts = 5;
    private reconnectDelay = 1000;
    private tokenManager: TokenManager;
    private url: string;
    private messageQueue: WebSocketMessage[] = [];
    private maxMessageQueueSize = 100;
    private connectionState = ConnectionState.DISCONNECTED;
    private connectTimeout: NodeJS.Timeout | null = null;
    private reconnectTimeout: NodeJS.Timeout | null = null;
    private pingInterval: NodeJS.Timeout | null = null;
    private lastPongTime: number = Date.now();
    private errorListeners: Set<(type: string, message: string) => void> = new Set();
    private statusListeners: Set<(status: string, message?: string) => void> = new Set();
    private shouldReconnect: boolean = true;
    private unsubscribeTokenRefresh: (() => void) | null = null;
    private connectionPromise: Promise<void> | null = null;
    private currentToken: string | null = null;

    constructor(baseUrl: string) {
        this.url = baseUrl;
        this.tokenManager = TokenManager.getInstance();
        
        // Register for token updates
        this.unsubscribeTokenRefresh = this.tokenManager.onTokenRefresh((token) => {
            this.handleTokenRefresh(token);
        });
    }

    private setState(state: ConnectionState, message?: string) {
        if (this.connectionState !== state) {
            this.connectionState = state;
            this.notifyStatus(state, message);
        }
    }

    private notifyStatus(status: string, message?: string) {
        this.statusListeners.forEach(listener => {
            try {
                listener(status, message);
            } catch (error) {
                console.error('Error in status listener:', error);
            }
        });
    }

    public onStatusChange(listener: (status: string, message?: string) => void): () => void {
        this.statusListeners.add(listener);
        return () => this.statusListeners.delete(listener);
    }

    private async handleTokenRefresh(token: string): Promise<void> {
        this.currentToken = token;
        
        if (this.isConnected()) {
            console.log("WebSocket is connected. Sending authentication message with new token.");
            this.send({
                type: WebSocketMessageType.AUTHENTICATE,
                data: { token: token }
            });
        } else if (this.connectionState === ConnectionState.DISCONNECTED) {
            console.log("WebSocket is disconnected. Reconnecting with new token.");
            await this.reconnectWithNewToken(token);
        }
    }

    public async reconnectWithNewToken(token: string): Promise<void> {
        // Cancel any pending operations
        this.cancelPendingOperations();
        
        // Close existing connection if it exists
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }

        // Reset state
        this.setState(ConnectionState.DISCONNECTED);
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
        this.currentToken = token;

        // Start fresh connection
        return this.connect(token);
    }

    private cancelPendingOperations() {
        if (this.connectTimeout) {
            clearTimeout(this.connectTimeout);
            this.connectTimeout = null;
        }
        if (this.reconnectTimeout) {
            clearTimeout(this.reconnectTimeout);
            this.reconnectTimeout = null;
        }
        this.stopPingInterval();
    }

    public async connect(token: string | null): Promise<void> {
        // If already connecting, return the existing promise
        if (this.connectionPromise) {
            return this.connectionPromise;
        }

        this.shouldReconnect = true;
        
        // Create and store the connection promise
        this.connectionPromise = this.performConnection(token);
        
        try {
            await this.connectionPromise;
        } finally {
            this.connectionPromise = null;
        }
    }

    private async performConnection(token: string | null): Promise<void> {
        // Don't proceed if already connected or connecting
        if (this.connectionState === ConnectionState.CONNECTED || 
            this.connectionState === ConnectionState.CONNECTING) {
            return;
        }

        this.setState(ConnectionState.CONNECTING, 'Attempting to connect...');

        // Get token if not provided
        if (!token) {
            token = this.currentToken || this.tokenManager.getCurrentToken();
        }

        // Validate token
        if (!token) {
            const isTokenValid = await this.tokenManager.isTokenValid();
            if (!isTokenValid) {
                console.log('Token is not valid, refreshing...');
                try {
                    token = await this.tokenManager.refreshToken();
                } catch (error) {
                    console.error('Failed to refresh token:', error);
                    this.setState(ConnectionState.ERROR, 'Authentication failed');
                    throw new Error('No valid authentication token available');
                }
            }
        }

        if (!token) {
            this.setState(ConnectionState.ERROR, 'No authentication token available');
            throw new Error('No authentication token available');
        }

        this.currentToken = token;

        return new Promise<void>((resolve, reject) => {
            try {
                const clientId = getOrCreateClientId();
                const fullUrl = `${this.url}/${clientId}?token=${token}`;
                
                console.log('WebSocket URL being used:', fullUrl);
                console.log('Attempting to connect to WebSocket:', fullUrl);
                
                this.ws = new WebSocket(fullUrl);

                const connectionTimeout = setTimeout(() => {
                    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) {
                        console.log('Connection timeout, closing WebSocket');
                        this.ws.close();
                        reject(new Error('Connection timeout'));
                    }
                }, 10000); // 10 second timeout

                this.ws.onopen = () => {
                    clearTimeout(connectionTimeout);
                    console.log('WebSocket opened. Client ID:', clientId);
                    
                    this.setState(ConnectionState.CONNECTED, 'Connection established');
                    this.reconnectAttempts = 0;
                    this.reconnectDelay = 1000;
                    this.startPingInterval();
                    
                    // Send any queued messages
                    this.flushMessageQueue();
                    
                    resolve();
                };

                this.ws.onclose = (event: CloseEvent) => {
                    clearTimeout(connectionTimeout);
                    this.stopPingInterval();
                    
                    const closeReason = event.reason || 'Connection closed';
                    const wasClean = event.wasClean;
                    
                    console.log('WebSocket closed:', { 
                        code: event.code, 
                        reason: closeReason, 
                        wasClean 
                    });

                    // If we were connecting and got closed, reject the promise
                    if (this.connectionState === ConnectionState.CONNECTING) {
                        reject(new Error(`Connection failed: ${closeReason}`));
                        return;
                    }
                    
                    this.setState(ConnectionState.DISCONNECTED, closeReason);
                    
                    // Only attempt to reconnect if shouldReconnect is true and not a clean close
                    if (this.shouldReconnect && !wasClean) {
                        this.attemptReconnect(closeReason);
                    }
                };

                this.ws.onerror = (error: WebSocketErrorEvent) => {
                    clearTimeout(connectionTimeout);
                    const errorMessage = error?.message || 'Unknown WebSocket Error';
                    
                    console.error('WebSocket error:', errorMessage, error);
                    
                    this.setState(ConnectionState.ERROR, errorMessage);
                    this.notifyError('connection_error', errorMessage);
                    
                    // If we were connecting and got an error, reject the promise
                    if (this.connectionState === ConnectionState.CONNECTING) {
                        reject(new Error(errorMessage));
                    }
                };

                this.ws.onmessage = this.handleMessage.bind(this);

            } catch (error) {
                console.error('WebSocket connection error:', error);
                this.setState(ConnectionState.ERROR, 'Failed to initialize WebSocket connection');
                reject(error);
            }
        });
    }

    private attemptReconnect(reason: string): void {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this.setState(ConnectionState.ERROR, 'Maximum reconnection attempts reached');
            this.notifyError('max_reconnect_attempts', 
                `Maximum reconnection attempts (${this.maxReconnectAttempts}) reached. Please refresh the page.`);
            return;
        }

        this.reconnectAttempts++;
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000); // Cap at 30 seconds
        
        this.setState(ConnectionState.RECONNECTING, 
            `Connection lost (${reason}). Attempting to reconnect...`);
        
        this.notifyError('connection_closed', 
            `Connection closed unexpectedly (${reason}). Attempting to reconnect in ${this.reconnectDelay/1000} seconds...`);

        this.reconnectTimeout = setTimeout(async () => {
            try {
                await this.performConnection(this.currentToken);
            } catch (error) {
                console.error('Reconnection failed:', error);
                // attemptReconnect will be called again by the onclose handler if appropriate
            }
        }, this.reconnectDelay);
    }

    private flushMessageQueue(): void {
        while (this.messageQueue.length > 0 && this.isConnected()) {
            const msg = this.messageQueue.shift();
            if (msg) {
                this.send(msg);
            }
        }
    }

    private startPingInterval(): void {
        console.log('Starting ping interval.');
        this.stopPingInterval();
        this.lastPongTime = Date.now();
        
        this.pingInterval = setInterval(() => {
            const now = Date.now();
            
            if (this.isConnected()) {
                this.send({
                    type: WebSocketMessageType.INTERNAL_PING,
                    data: {},
                    timestamp: now
                });
            }
            
            // Check if we haven't received a pong in too long
            if (now - this.lastPongTime > 90000) { // 90 seconds
                this.handleConnectionTimeout();
            }
        }, 15000); // Send ping every 15 seconds
    }

    private stopPingInterval(): void {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    private handleConnectionTimeout(): void {
        console.log('Connection timeout detected. Closing WebSocket.');
        if (this.ws) {
            this.ws.close();
            // Reconnection will be handled by onclose handler
        }
    }

    private handleMessage(event: MessageEvent): void {
        try {
            const message = JSON.parse(event.data) as WebSocketMessage<unknown>;
            
            // Handle ping/pong messages
            if (message.type === WebSocketMessageType.PONG) {
                this.lastPongTime = Date.now();
                return;
            } else if (message.type === WebSocketMessageType.PING) {
                // Respond to server's ping with a pong
                this.send({
                    type: WebSocketMessageType.PONG,
                    data: { timestamp: Date.now() }
                });
                return;
            }

            // Validate message structure
            if (!Object.values(WebSocketMessageType).includes(message.type as WebSocketMessageType)) {
                console.error('Invalid message type received:', message.type);
                return;
            }

            // Handle message with registered listeners
            const type = message.type as WebSocketMessageType;
            if (this.listeners.has(type)) {
                this.listeners.get(type)?.forEach((listener: MessageListener<unknown>) => {
                    try {
                        listener(message.data);
                    } catch (error) {
                        console.error(`Error in listener for message type ${type}:`, error);
                    }
                });
            }
        } catch (error) {
            console.error('Error handling WebSocket message:', error);
        }
    }

    public subscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>): void {
        if (!this.listeners.has(messageType)) {
            this.listeners.set(messageType, new Set());
        }
        this.listeners.get(messageType)?.add(listener as MessageListener<unknown>);
    }

    public unsubscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>): void {
        this.listeners.get(messageType)?.delete(listener as MessageListener<unknown>);
        if (this.listeners.get(messageType)?.size === 0) {
            this.listeners.delete(messageType);
        }
    }

    public send(message: WebSocketMessage): void {
        // Transform internal ping to proper ping for the server
        if (message.type === WebSocketMessageType.INTERNAL_PING) {
            const pingMessage: WebSocketMessage = {
                type: WebSocketMessageType.PING,
                data: {},
                timestamp: message.timestamp ?? Date.now()
            };
            
            if (this.isConnected()) {
                this.ws!.send(JSON.stringify(pingMessage));
            }
            return;
        }

        // Ensure message has a timestamp
        const messageToSend: WebSocketMessage = {
            ...message,
            timestamp: message.timestamp ?? Date.now()
        };

        if (this.isConnected()) {
            this.ws!.send(JSON.stringify(messageToSend));
        } else {
            // Queue message if not connected
            if (this.messageQueue.length >= this.maxMessageQueueSize) {
                this.messageQueue.shift(); // Remove the oldest message
                console.warn('WebSocket message queue full. Dropping oldest message.');
            }
            this.messageQueue.push(messageToSend);
        }
    }

    public disconnect(): void {
        console.log('Disconnecting WebSocket...');
        this.shouldReconnect = false;
        this.cancelPendingOperations();
        
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        
        this.setState(ConnectionState.DISCONNECTED, 'User disconnected');
    }

    public destroy(): void {
        console.log('Destroying WebSocket client...');
        this.disconnect();
        
        // Clear all listeners to prevent memory leaks
        this.listeners.clear();
        this.errorListeners.clear();
        this.statusListeners.clear();
        
        // Unsubscribe from TokenManager refresh events
        if (this.unsubscribeTokenRefresh) {
            this.unsubscribeTokenRefresh();
            this.unsubscribeTokenRefresh = null;
        }
        
        // Clear message queue
        this.messageQueue.length = 0;
    }

    public isConnected(): boolean {
        return this.ws !== null && 
               this.ws.readyState === WebSocket.OPEN && 
               this.connectionState === ConnectionState.CONNECTED;
    }

    public getConnectionState(): ConnectionState {
        return this.connectionState;
    }

    public onError(listener: (type: string, message: string) => void): () => void {
        this.errorListeners.add(listener);
        return () => this.errorListeners.delete(listener);
    }

    private notifyError(type: string, message: string): void {
        this.errorListeners.forEach(listener => {
            try {
                listener(type, message);
            } catch (error) {
                console.error('Error in error listener:', error);
            }
        });

        // Display user-friendly message using errorNotifier
        let userMessage = message;
        if (type === 'auth_error') {
            userMessage = 'Authentication failed. Please log in again.';
        } else if (type === 'max_reconnect_attempts') {
            userMessage = 'Lost connection to the server. Please refresh the page.';
        } else if (type === 'connection_closed') {
            userMessage = 'Connection to server lost. Attempting to reconnect...';
        } else if (type === 'connection_error') {
            userMessage = 'Failed to connect to the server. Please check your network.';
        }
        
        errorNotifier.error(userMessage);
    }
}