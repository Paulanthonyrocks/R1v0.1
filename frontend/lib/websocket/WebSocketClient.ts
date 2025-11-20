import { TokenManager } from '../auth/TokenManager';
import { errorNotifier } from '../utils/errorNotifier';

interface WebSocketErrorEvent extends Event {
    message?: string;
}

const getOrCreateClientId = () => {
    if (typeof window === 'undefined') return '';
    let clientId = localStorage.getItem('ws_client_id');
    if (!clientId) {
        clientId = crypto.randomUUID();
        localStorage.setItem('ws_client_id', clientId);
    }
    return clientId;
};

type MessageListener<T> = (data: T) => void;

export enum WebSocketMessageType {
    METRICS_UPDATE = 'metrics_update',
    KPI_UPDATE = 'kpi_update',
    NEW_ALERT = 'new_alert',
    SIGNAL_UPDATE = 'signal_update',
    VIDEO_FRAME = 'video_frame',
    VIDEO_UPDATE = 'video_update',
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
    GET_INITIAL_FEED_STATUSES = 'get_initial_feed_statuses',
    START_FEED = 'start_feed',
    STOP_FEED = 'stop_feed'
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
    subscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>): () => void;
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
    private videoWorker: Worker | null = null;
    private requiresClientId: boolean;

    constructor(baseUrl: string, requiresClientId = true) {
        this.url = baseUrl;
        this.requiresClientId = requiresClientId;
        this.tokenManager = TokenManager.getInstance();
        
        if (typeof window !== 'undefined' && this.requiresClientId) {
            this.videoWorker = new Worker('/workers/video-worker.js');
            this.videoWorker.onmessage = (e) => {
                if (e.data.error) {
                    console.error('Worker: Frame decoding failed', e.data.error);
                } else {
                    const { feed_id, frame } = e.data;
                    this.notifyListeners(WebSocketMessageType.VIDEO_FRAME, { feed_id, frame });
                }
            };
        }

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
            this.send({ type: WebSocketMessageType.AUTHENTICATE, data: { token } });
        } else if (this.connectionState === ConnectionState.DISCONNECTED) {
            console.log("WebSocket is disconnected. Reconnecting with new token.");
            await this.reconnectWithNewToken(token);
        }
    }

    public async reconnectWithNewToken(token: string): Promise<void> {
        this.cancelPendingOperations();
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.setState(ConnectionState.DISCONNECTED);
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
        this.currentToken = token;
        return this.connect(token);
    }

    private cancelPendingOperations() {
        if (this.connectTimeout) clearTimeout(this.connectTimeout);
        if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
        this.stopPingInterval();
    }

    public async connect(token: string | null): Promise<void> {
        if (this.connectionPromise) return this.connectionPromise;
        this.shouldReconnect = true;
        this.connectionPromise = this.performConnection(token);
        try {
            await this.connectionPromise;
        } finally {
            this.connectionPromise = null;
        }
    }

    private async performConnection(token: string | null): Promise<void> {
        if (this.connectionState === ConnectionState.CONNECTED || this.connectionState === ConnectionState.CONNECTING) {
            return;
        }

        this.setState(ConnectionState.CONNECTING, 'Attempting to connect...');

        if (!token) token = this.currentToken || this.tokenManager.getCurrentToken();

        if (!token) {
            console.warn('No auth token for WebSocket, will wait for token update.');
            this.notifyError('auth_error', 'No authentication token available.');
            return;
        }

        this.currentToken = token;

        return new Promise<void>((resolve, reject) => {
            try {
                const clientId = this.requiresClientId ? getOrCreateClientId() : null;
                const url = new URL(this.url);
                if (clientId) url.pathname += `/${clientId}`;
                url.searchParams.set('token', token as string);
                
                console.log('Attempting to connect to WebSocket:', url.toString());
                
                this.ws = new WebSocket(url.toString());
                this.ws.binaryType = 'arraybuffer';

                const connectionTimeout = setTimeout(() => {
                    if (this.ws?.readyState === WebSocket.CONNECTING) {
                        this.ws.close();
                        reject(new Error('Connection timeout'));
                    }
                }, 10000);

                this.ws.onopen = () => {
                    clearTimeout(connectionTimeout);
                    console.log(`WebSocket opened. ${clientId ? `Client ID: ${clientId}` : ''}`);
                    this.setState(ConnectionState.CONNECTED, 'Connection established');
                    this.reconnectAttempts = 0;
                    this.reconnectDelay = 1000;
                    this.startPingInterval();
                    this.flushMessageQueue();
                    resolve();
                };

                this.ws.onclose = (event: CloseEvent) => {
                    clearTimeout(connectionTimeout);
                    this.stopPingInterval();
                    const { reason, wasClean, code } = event;
                    console.log('WebSocket closed:', { code, reason, wasClean });

                    if (this.connectionState === ConnectionState.CONNECTING) {
                        reject(new Error(`Connection failed: ${reason}`));
                        return;
                    }
                    
                    this.setState(ConnectionState.DISCONNECTED, reason);
                    if (this.shouldReconnect && !wasClean) this.attemptReconnect(reason);
                };

                this.ws.onerror = (event: Event) => {
                    clearTimeout(connectionTimeout);
                    const error = event as WebSocketErrorEvent;
                    const errorMessage = error?.message || 'Unknown WebSocket Error';
                    console.error(`WebSocket error: "${errorMessage}". This is often a generic error. For more details, check your browser\'s developer console for a failed WebSocket "Upgrade" request in the Network tab. The response headers or console logs from the server on initial connection might provide more insight.`, error);
                    this.setState(ConnectionState.ERROR, errorMessage);
                    this.notifyError('connection_error', errorMessage);
                    if (this.connectionState === ConnectionState.CONNECTING) reject(new Error(errorMessage));
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
            this.notifyError('max_reconnect_attempts', `Maximum reconnection attempts (${this.maxReconnectAttempts}) reached. Please refresh the page.`);
            return;
        }

        this.reconnectAttempts++;
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
        this.setState(ConnectionState.RECONNECTING, `Connection lost (${reason}). Attempting to reconnect...`);
        this.notifyError('connection_closed', `Connection closed unexpectedly (${reason}). Attempting to reconnect in ${this.reconnectDelay / 1000} seconds...`);

        this.reconnectTimeout = setTimeout(() => this.performConnection(this.currentToken), this.reconnectDelay);
    }

    private flushMessageQueue(): void {
        while (this.messageQueue.length > 0 && this.isConnected()) {
            const msg = this.messageQueue.shift();
            if (msg) this.send(msg);
        }
    }

    private startPingInterval(): void {
        this.stopPingInterval();
        this.lastPongTime = Date.now();
        this.pingInterval = setInterval(() => {
            if (this.isConnected()) {
                this.send({ type: WebSocketMessageType.INTERNAL_PING, timestamp: Date.now() });
            }
            if (Date.now() - this.lastPongTime > 90000) this.handleConnectionTimeout();
        }, 15000);
    }

    private stopPingInterval(): void {
        if (this.pingInterval) clearInterval(this.pingInterval);
    }

    private handleConnectionTimeout(): void {
        console.log('Connection timeout detected. Closing WebSocket.');
        this.ws?.close();
    }

    private handleMessage(event: MessageEvent): void {
        if (typeof event.data === 'string') {
            if (event.data === 'ping') {
                this.ws?.send('pong');
                return;
            }
            if (event.data === 'pong') {
                this.lastPongTime = Date.now();
                return;
            }
            try {
                const message = JSON.parse(event.data) as WebSocketMessage<unknown>;
                if (message.type === WebSocketMessageType.PONG) {
                    this.lastPongTime = Date.now();
                    return;
                }
                this.notifyListeners(message.type, message.data);
            } catch (error) {
                console.error('Error handling WebSocket message:', error, event.data);
            }
        } else if (event.data instanceof ArrayBuffer) {
            // Handle binary frame data (for video feeds that don't use workers)
            this.notifyListeners(WebSocketMessageType.VIDEO_FRAME, { frame: event.data });
        }
    }

    private notifyListeners<T>(type: WebSocketMessageType, data: T): void {
        this.listeners.get(type)?.forEach((listener: MessageListener<T>) => {
            try {
                listener(data);
            } catch (error) {
                console.error(`Error in listener for message type ${type}:`, error);
            }
        });
    }

    public subscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>): () => void {
        if (!this.listeners.has(messageType)) this.listeners.set(messageType, new Set());
        this.listeners.get(messageType)?.add(listener as MessageListener<unknown>);
        return () => this.unsubscribe(messageType, listener);
    }

    public unsubscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>): void {
        this.listeners.get(messageType)?.delete(listener as MessageListener<unknown>);
    }

    public send(message: WebSocketMessage): void {
        const messageToSend: WebSocketMessage = { ...message, timestamp: message.timestamp ?? Date.now() };
        if (this.isConnected()) {
            this.ws!.send(JSON.stringify(messageToSend));
        } else {
            if (this.messageQueue.length >= this.maxMessageQueueSize) {
                this.messageQueue.shift();
                console.warn('WebSocket message queue full. Dropping oldest message.');
            }
            this.messageQueue.push(messageToSend);
        }
    }

    public disconnect(): void {
        console.log('Disconnecting WebSocket...');
        this.shouldReconnect = false;
        this.cancelPendingOperations();
        this.ws?.close();
        this.setState(ConnectionState.DISCONNECTED, 'User disconnected');
    }

    public destroy(): void {
        console.log('Destroying WebSocket client...');
        this.disconnect();
        this.listeners.clear();
        this.errorListeners.clear();
        this.statusListeners.clear();
        this.unsubscribeTokenRefresh?.();
        this.videoWorker?.terminate();
        this.messageQueue.length = 0;
    }

    public isConnected(): boolean {
        return this.ws !== null && this.ws.readyState === WebSocket.OPEN && this.connectionState === ConnectionState.CONNECTED;
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
