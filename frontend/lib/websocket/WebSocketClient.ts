import { TokenManager } from '../auth/TokenManager';
import { errorNotifier } from '../utils/errorNotifier';

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
// Define possible metric value types
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
    INITIAL_FEED_STATUSES = 'initial_feed_statuses', // Added missing type
    PONG = 'pong',
    AUTH_SUCCESS = 'auth_success',
    AUTH_FAILURE = 'auth_failure',
    // Special type for internal ping messages that will be translated to pong responses
    INTERNAL_PING = '__internal_ping',
    PING = 'ping'
}

export interface WebSocketMessage<T = unknown> {
    type: WebSocketMessageType;
    data?: T | null; // Make data optional and allow null
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

export class WebSocketClient implements IWebSocketClient {
    private listeners: Map<WebSocketMessageType, Set<MessageListener<unknown>>> = new Map();
    private ws: WebSocket | null = null;
    private reconnectAttempts = 0;
    private maxReconnectAttempts = 5;
    private reconnectDelay = 1000; // Start with 1 second
    private tokenManager: TokenManager;
    private url: string;
    private messageQueue: WebSocketMessage[] = [];
    private maxMessageQueueSize = 100; // Limit the queue size to 100 messages
    private isConnecting = false;
    private pingInterval: NodeJS.Timeout | null = null;
    private lastPongTime: number = Date.now();
    private errorListeners: Set<(type: string, message: string) => void> = new Set();
    private statusListeners: Set<(status: string, message?: string) => void> = new Set();

    constructor(baseUrl: string) {
        this.url = baseUrl;
        this.tokenManager = TokenManager.getInstance();
        
        // Register for token updates
        this.tokenManager.onTokenRefresh((token) => {
            this.handleTokenRefresh(token);
        });
    }

    private notifyStatus(status: string, message?: string) {
        this.statusListeners.forEach(listener => listener(status, message));
    }

    public onStatusChange(listener: (status: string, message?: string) => void): () => void {
        this.statusListeners.add(listener);
        return () => this.statusListeners.delete(listener);
    }

    private async handleTokenRefresh(token: string): Promise<void> {
        // Always attempt to reconnect with the new token, regardless of current WebSocket state.
        // This ensures that even if the WebSocket was closing or closed, it tries to re-establish
        // with the fresh token.
        await this.reconnectWithNewToken(token);
    }

    public async reconnectWithNewToken(token: string): Promise<void> {
        // Close existing connection
        if (this.ws) {
            this.ws.close();
        }

        // Reset reconnection parameters
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;

        // Attempt to establish new connection
        await this.connect(token);
    }

    public async connect(token: string | null): Promise<void> {
        if (this.isConnecting) return;
        this.isConnecting = true;
        this.notifyStatus('connecting', 'Attempting to connect...');

        // If no token is provided, try to get it from TokenManager
        if (!token) {
            token = this.tokenManager.getCurrentToken();
            if (!token) {
                // If still no token, wait and retry
                console.warn('No token available for WebSocket connection. Retrying in 2 seconds...');
                this.isConnecting = false; // Allow new connection attempts
                this.notifyStatus('error', 'No authentication token available.');
                setTimeout(() => this.connect(null), 2000); // Retry after 2 seconds
                return;
            }
        }

        try {
            const clientId = getOrCreateClientId();
            const fullUrl = `${this.url}/${clientId}?token=${token}`;
            console.log('WebSocket URL being used:', fullUrl);
            console.log('Attempting to connect to WebSocket:', fullUrl);
            this.ws = new WebSocket(fullUrl);

            this.ws.onopen = () => {
                console.log('WebSocket opened. Client ID:', clientId);
                this.isConnecting = false;
                this.reconnectAttempts = 0;
                this.reconnectDelay = 1000;
                this.startPingInterval();
                this.notifyStatus('connected', 'Connection established.');
                
                // Send any queued messages
                while (this.messageQueue.length > 0) {
                    const msg = this.messageQueue.shift();
                    if (msg) {
                        this.send(msg);
                    }
                }
            };

            this.ws.onclose = async (event: CloseEvent) => {
                this.isConnecting = false;
                this.stopPingInterval();
                
                const closeReason = event.reason || 'Connection closed';
                const wasClean = event.wasClean;
                
                if (this.reconnectAttempts < this.maxReconnectAttempts) {
                    this.reconnectAttempts++;
                    this.reconnectDelay *= 2; // Exponential backoff
                    const delay = this.reconnectDelay;
                    
                    if (!wasClean) {
                        this.notifyError('connection_closed', 
                            `Connection closed unexpectedly (${closeReason}). Attempting to reconnect in ${delay/1000} seconds...`);
                        this.notifyStatus('reconnecting', `Connection lost. Attempting to reconnect...`);
                    }
                    
                    setTimeout(async () => {
                        const currentToken = this.tokenManager.getCurrentToken();
                        if (currentToken) {
                            await this.connect(currentToken);
                        } else {
                            this.notifyError('auth_error', 'Unable to reconnect: No valid authentication token available');
                            this.notifyStatus('error', 'Authentication expired. Please log in again.');
                        }
                    }, delay);
                } else {
                    this.notifyError('max_reconnect_attempts', 
                        `Maximum reconnection attempts (${this.maxReconnectAttempts}) reached. Please refresh the page.`);
                    this.notifyStatus('disconnected', 'Could not reconnect to the server.');
                }
            };

            this.ws.onerror = (error: Event) => {
                const wsError = error as any; // Cast to any to access potential non-standard properties
                const errorMessage = wsError.message || 'Unknown WebSocket Error';
                const errorType = wsError.type || 'unknown';
                console.error('WebSocket error:', errorMessage, errorType, error);
                this.isConnecting = false;
                
                // Emit error notification to listeners
                this.notifyError('connection_error', errorMessage);
            };

            this.ws.onmessage = this.handleMessage.bind(this);

        } catch (error) {
            console.error('WebSocket connection error:', error);
            this.isConnecting = false;
            this.notifyStatus('error', 'Failed to initialize WebSocket connection.');
        }
    }

    private startPingInterval(): void {
        console.log('Starting ping interval.');
        this.stopPingInterval();
        this.pingInterval = setInterval(() => {
            const now = Date.now();
            this.send({
                type: WebSocketMessageType.INTERNAL_PING,
                data: {}, // Send an empty object for data
                timestamp: now
            });
            
            // Check if we haven't received a pong in too long
            if (now - this.lastPongTime > 30000) { // 30 seconds
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
            
            // Handle pong messages
            if (message.type === WebSocketMessageType.PONG) {
                this.lastPongTime = Date.now();
                return;
            }

            // Validate message structure
            if (!Object.values(WebSocketMessageType).includes(message.type as WebSocketMessageType)) {
                console.error('Invalid message type received:', message.type, 'Expected one of:', Object.values(WebSocketMessageType));
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
                type: WebSocketMessageType.PING, // Send PING to the server
                data: {}, // Send an empty object for data
                timestamp: message.timestamp ?? Date.now()
            };
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(pingMessage));
            }
            return;
        }

        // Ensure message has a timestamp
        const messageToSend: WebSocketMessage = {
            ...message,
            timestamp: message.timestamp ?? Date.now()
        };

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(messageToSend));
        } else {
            // If queue is full, remove the oldest message
            if (this.messageQueue.length >= this.maxMessageQueueSize) {
                this.messageQueue.shift(); // Remove the oldest message
                console.warn('WebSocket message queue full. Dropping oldest message.');
            }
            // Queue message if not connected
            this.messageQueue.push(messageToSend);
        }
    }

    public disconnect(): void {
        this.stopPingInterval();
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.notifyStatus('disconnected', 'User disconnected.');
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

        // Also emit as an error notification message
        this.send({
            type: WebSocketMessageType.ERROR_NOTIFICATION,
            data: {
                error_code: type,
                message: message,
                details: type === 'connection_error' ? message : undefined, // Include message as details for connection_error
                timestamp: new Date().toISOString()
            } as ErrorNotification
        });
    }
}
