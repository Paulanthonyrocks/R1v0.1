import { TokenManager } from '../auth/TokenManager';

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

export interface WebSocketMessage<T = unknown> {
    type: string;
    payload?: T;
    timestamp?: number;
}

export interface IWebSocketClient {
    connect(token: string): Promise<void>;
    disconnect(): void;
    send(data: WebSocketMessage<unknown>): void;
    subscribe<T>(messageType: string, listener: MessageListener<T>): void;
    unsubscribe<T>(messageType: string, listener: MessageListener<T>): void;
    reconnectWithNewToken(token: string): Promise<void>;
}

export class WebSocketClient implements IWebSocketClient {
    private listeners: Map<string, Set<MessageListener<unknown>>> = new Map();
    private ws: WebSocket | null = null;
    private reconnectAttempts = 0;
    private maxReconnectAttempts = 5;
    private reconnectDelay = 1000; // Start with 1 second
    private tokenManager: TokenManager;
    private url: string;
    private messageQueue: WebSocketMessage[] = [];
    private isConnecting = false;
    private pingInterval: NodeJS.Timeout | null = null;
    private lastPongTime: number = Date.now();

    constructor(baseUrl: string) {
        this.url = baseUrl;
        this.tokenManager = TokenManager.getInstance();
        
        // Register for token updates
        this.tokenManager.onTokenRefresh((token) => {
            this.handleTokenRefresh(token);
        });
    }

    private async handleTokenRefresh(token: string): Promise<void> {
        // If we have an active connection, need to reconnect with new token
        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
            await this.reconnectWithNewToken(token);
        }
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

    public async connect(token: string): Promise<void> {
        if (this.isConnecting) return;
        this.isConnecting = true;

        try {
            const clientId = getOrCreateClientId();
            const fullUrl = `${this.url}/${clientId}?token=${token}`;
            this.ws = new WebSocket(fullUrl);

            this.ws.onopen = () => {
                this.isConnecting = false;
                this.reconnectAttempts = 0;
                this.reconnectDelay = 1000;
                this.startPingInterval();
                
                // Send any queued messages
                while (this.messageQueue.length > 0) {
                    const msg = this.messageQueue.shift();
                    if (msg) {
                        this.send(msg);
                    }
                }
            };

            this.ws.onclose = async () => {
                this.isConnecting = false;
                this.stopPingInterval();
                
                if (this.reconnectAttempts < this.maxReconnectAttempts) {
                    this.reconnectAttempts++;
                    this.reconnectDelay *= 2; // Exponential backoff
                    setTimeout(async () => {
                        const currentToken = this.tokenManager.getCurrentToken();
                        if (currentToken) {
                            await this.connect(currentToken);
                        }
                    }, this.reconnectDelay);
                }
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.isConnecting = false;
            };

            this.ws.onmessage = this.handleMessage.bind(this);

        } catch (error) {
            console.error('WebSocket connection error:', error);
            this.isConnecting = false;
        }
    }

    private startPingInterval(): void {
        this.stopPingInterval();
        this.pingInterval = setInterval(() => {
            this.send({
                type: 'ping',
                payload: { timestamp: Date.now() }
            });
            
            // Check if we haven't received a pong in too long
            if (Date.now() - this.lastPongTime > 30000) { // 30 seconds
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
        if (this.ws) {
            this.ws.close();
            // Reconnection will be handled by onclose handler
        }
    }

    private handleMessage(event: MessageEvent): void {
        try {
            const message = JSON.parse(event.data);
            
            // Handle pong messages
            if (message.type === 'pong') {
                this.lastPongTime = Date.now();
                return;
            }

            // Handle message with registered listeners
            if (typeof message === 'object' && message !== null && 'type' in message) {
                const { type, payload } = message;
                if (this.listeners.has(type)) {
                    this.listeners.get(type)?.forEach((listener: MessageListener<unknown>) => {
                        try {
                            listener(payload);
                        } catch (error) {
                            console.error(`Error in listener for message type ${type}:`, error);
                        }
                    });
                }
            }
        } catch (error) {
            console.error('Error handling WebSocket message:', error);
        }
    }

    public subscribe<T>(messageType: string, listener: MessageListener<T>): void {
        if (!this.listeners.has(messageType)) {
            this.listeners.set(messageType, new Set());
        }
        this.listeners.get(messageType)?.add(listener as MessageListener<unknown>);
    }

    public unsubscribe<T>(messageType: string, listener: MessageListener<T>): void {
        this.listeners.get(messageType)?.delete(listener as MessageListener<unknown>);
        if (this.listeners.get(messageType)?.size === 0) {
            this.listeners.delete(messageType);
        }
    }

    public send(data: WebSocketMessage): void {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        } else {
            // Queue message if not connected
            this.messageQueue.push(data);
        }
    }

    public disconnect(): void {
        this.stopPingInterval();
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
