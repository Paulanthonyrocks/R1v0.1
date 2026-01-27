import { TokenManager } from '../auth/TokenManager';
import { errorNotifier } from '../utils/errorNotifier';

interface WebSocketErrorEvent extends Event {
    message?: string;
}

const getOrCreateClientId = () => {
    if (typeof window === 'undefined') return '';
    
    try {
        const storedId = sessionStorage.getItem('ws_client_id');
        if (storedId) {
            return storedId;
        }
        const newId = crypto.randomUUID();
        sessionStorage.setItem('ws_client_id', newId);
        return newId;
    } catch (e) {
        console.warn('Failed to access sessionStorage:', e);
        return crypto.randomUUID();
    }
};

type MessageListener<T> = (data: T) => void;

interface ScopedListener {
    scope: string;
    listener: MessageListener<unknown>;
}

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
    UNSUBSCRIBE_FROM_FEED = 'unsubscribe_from_feed',
    UPDATE_FEED_CONFIG = 'update_feed_config',
    GET_INITIAL_FEED_STATUSES = 'get_initial_feed_statuses',
    START_FEED = 'start_feed',
    STOP_FEED = 'stop_feed',
    RESTART_FEED = 'restart_feed',
    SNAPSHOT_READY = 'snapshot_ready',
    INTERNAL_PONG = '__internal_pong'
}

export interface WebSocketMessage<T = unknown> {
    type: WebSocketMessageType;
    data?: T | null;
    client_id?: string;
    correlation_id?: string;
    timestamp?: number;
}

export interface IWebSocketClient {
    activate(): void;
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

// Global tracking to ensure only the latest instance in a tab is active
// This prevents infinite loops if multiple instances are created (e.g. during HMR or React Strict Mode)
let lastActiveInstanceId: string | null = null;

export class WebSocketClient implements IWebSocketClient {
    private listeners: Map<WebSocketMessageType, Set<MessageListener<unknown> | ScopedListener>> = new Map();
    private ws: WebSocket | null = null;
    private reconnectAttempts = 0;
    private maxReconnectAttempts = 5;
    private reconnectDelay = 1000;
    private instanceId = Math.random().toString(36).substring(7);

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
    private clientId: string | null = null;

    constructor(baseUrl: string, requiresClientId = true) {
        this.url = baseUrl;
        this.requiresClientId = requiresClientId;
        this.tokenManager = TokenManager.getInstance();
        this.clientId = this.requiresClientId ? getOrCreateClientId() : null;
        
        if (typeof window !== 'undefined') {
            console.debug(`[WebSocketClient ${this.instanceId}] Instantiated. Client ID: ${this.clientId}`);
        }
    }

    /**
     * Activates the client, claiming it as the primary active instance in this tab.
     * This should be called when the client is mounted in the UI.
     */
    public activate(): void {
        if (typeof window === 'undefined') return;
        
        if (this.isInstanceActive()) {
            console.debug(`[WebSocketClient ${this.instanceId}] Already active.`);
            return;
        }

        lastActiveInstanceId = this.instanceId;
        console.log(`[WebSocketClient ${this.instanceId}] Activated. Client ID: ${this.clientId}`);
        
        // Setup Worker if needed
        if (this.requiresClientId && !this.videoWorker) {
            this.videoWorker = new Worker('/workers/video-worker.js');
            this.videoWorker.onmessage = (e) => {
                if (e.data.error) {
                    console.error(`[WebSocketClient ${this.instanceId}] Worker: Frame decoding failed`, e.data.error);
                } else {
                    // e.data contains feed_id, frame (ArrayBuffer), metrics, vehicles, etc.
                    this.notifyListeners(WebSocketMessageType.VIDEO_FRAME, e.data, e.data.feed_id);
                }
            };
        }

        // Setup Token Refresh listener
        if (!this.unsubscribeTokenRefresh) {
            this.unsubscribeTokenRefresh = this.tokenManager.onTokenRefresh((token) => {
                this.handleTokenRefresh(token).catch(e => {
                    console.error(`[WebSocketClient ${this.instanceId}] Error in handleTokenRefresh:`, e);
                });
            });
        }
    }

    public getInstanceId(): string {
        return this.instanceId;
    }

    private isInstanceActive(): boolean {
        return lastActiveInstanceId === this.instanceId;
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
                console.error(`[WebSocketClient ${this.instanceId}] Error in status listener:`, error);
            }
        });
    }

    public onStatusChange(listener: (status: string, message?: string) => void): () => void {
        this.statusListeners.add(listener);
        // Immediately notify the listener of the current state
        try {
            listener(this.connectionState, "Initial state on subscription");
        } catch (error) {
            console.error(`[WebSocketClient ${this.instanceId}] Error in initial status notification:`, error);
        }
        return () => this.statusListeners.delete(listener);
    }

    private async handleTokenRefresh(token: string): Promise<void> {
        this.currentToken = token;
        
        if (!this.isInstanceActive()) {
            console.debug(`[WebSocketClient ${this.instanceId}] Instance dormant. Ignoring token refresh.`);
            return;
        }

        if (this.isConnected()) {
            console.log(`[WebSocketClient ${this.instanceId}] WebSocket is connected. Sending re-authentication.`);
            this.send({ type: WebSocketMessageType.AUTHENTICATE, data: { token } });
        } else if (this.connectionState === ConnectionState.DISCONNECTED || this.connectionState === ConnectionState.ERROR) {
            console.log(`[WebSocketClient ${this.instanceId}] WebSocket is ${this.connectionState}. Reconnecting with new token.`);
            try {
                await this.reconnectWithNewToken(token);
            } catch (e) {
                console.error(`[WebSocketClient ${this.instanceId}] Reconnection with new token failed:`, e);
            }
        }
    }

    public async reconnectWithNewToken(token: string): Promise<void> {
        if (!this.isInstanceActive()) return;

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
        if (!this.isInstanceActive()) {
            console.warn(`[WebSocketClient ${this.instanceId}] Attempted to connect from dormant instance. Aborting.`);
            return;
        }
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

        if (!this.isInstanceActive()) return;

        this.setState(ConnectionState.CONNECTING, 'Attempting to connect...');

        if (!token) token = this.currentToken || this.tokenManager.getCurrentToken();

        if (!token) {
            console.warn(`[WebSocketClient ${this.instanceId}] No auth token for WebSocket, will wait for token update.`);
            this.notifyError('auth_error', 'No authentication token available.');
            return;
        }

        this.currentToken = token;

        return new Promise<void>((resolve, reject) => {
            try {
                if (!this.isInstanceActive()) {
                    reject(new Error('Instance became dormant during connection setup'));
                    return;
                }

                const clientId = this.clientId;
                const url = new URL(this.url);

                if (clientId) {
                    // Safely join path segments
                    url.pathname = [url.pathname.replace(/\/$/, ''), clientId]
                        .filter(Boolean)
                        .join('/');
                }
                
                url.searchParams.set('token', token as string);
                
                console.log(`[WebSocketClient ${this.instanceId}] Attempting to connect to WebSocket:`, url.toString());
                
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
                    
                    if (!this.shouldReconnect || !this.isInstanceActive()) {
                        console.debug(`[WebSocketClient ${this.instanceId}] Connection opened but instance is dormant or destroying. Closing.`);
                        this.ws?.close();
                        return;
                    }

                    console.log(`[WebSocketClient ${this.instanceId}] WebSocket opened. ${clientId ? `Client ID: ${clientId}` : ''}`);
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
                    console.log(`[WebSocketClient ${this.instanceId}] WebSocket closed:`, { code, reason, wasClean });

                    if (this.connectionState === ConnectionState.CONNECTING) {
                        reject(new Error(`Connection failed: ${reason}`));
                        return;
                    }
                    
                    this.setState(ConnectionState.DISCONNECTED, reason);
                    if (this.shouldReconnect && !wasClean && this.isInstanceActive()) {
                        this.attemptReconnect(reason);
                    }
                };

                this.ws.onerror = (event: Event) => {
                    clearTimeout(connectionTimeout);
                    const error = event as WebSocketErrorEvent;
                    const errorMessage = error?.message || 'Unknown WebSocket Error';
                    console.error(`[WebSocketClient ${this.instanceId}] WebSocket error: "${errorMessage}".`, error);
                    this.setState(ConnectionState.ERROR, errorMessage);
                    this.notifyError('connection_error', errorMessage);
                    if (this.connectionState === ConnectionState.CONNECTING) reject(new Error(errorMessage));
                };

                this.ws.onmessage = this.handleMessage.bind(this);

            } catch (error) {
                console.error(`[WebSocketClient ${this.instanceId}] WebSocket connection error:`, error);
                this.setState(ConnectionState.ERROR, 'Failed to initialize WebSocket connection');
                reject(error);
            }
        });
    }

    private attemptReconnect(reason: string): void {
        if (!this.isInstanceActive()) return;

        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this.setState(ConnectionState.ERROR, 'Maximum reconnection attempts reached');
            this.notifyError('max_reconnect_attempts', `Maximum reconnection attempts reached. Please refresh the page.`);
            return;
        }

        this.reconnectAttempts++;
        // Add exponential backoff with jitter (±20%)
        const jitter = 0.8 + Math.random() * 0.4;
        this.reconnectDelay = Math.min(this.reconnectDelay * 2 * jitter, 30000);
        
        console.log(`[WebSocketClient ${this.instanceId}] Reconnect attempt ${this.reconnectAttempts} in ${Math.round(this.reconnectDelay)}ms. Reason: ${reason}`);
        
        this.setState(ConnectionState.RECONNECTING, `Connection lost (${reason}). Attempting to reconnect...`);
        this.notifyError('connection_closed', `Connection closed unexpectedly (${reason}). Reconnecting...`);

        this.reconnectTimeout = setTimeout(() => {
            if (this.isInstanceActive()) {
                this.performConnection(this.currentToken).catch(e => {
                    console.error(`[WebSocketClient ${this.instanceId}] Reconnect performConnection failed:`, e);
                });
            }
        }, this.reconnectDelay);
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
        console.debug(`[WebSocketClient ${this.instanceId}] Starting ping interval`);
        
        this.pingInterval = setInterval(() => {
            if (!this.isConnected()) {
                console.warn(`[WebSocketClient ${this.instanceId}] Ping interval running but socket not connected. Stopping.`);
                this.stopPingInterval();
                return;
            }

            // Sending a PING to the server, expecting a PONG back
            console.debug(`[WebSocketClient ${this.instanceId}] Sending PING`);
            this.send({ type: WebSocketMessageType.PING, timestamp: Date.now() });

            // If no pong is received within 120 seconds, consider connection timed out
            const timeSinceLastPong = Date.now() - this.lastPongTime;
            if (timeSinceLastPong > 120000) {
                 console.warn(`[WebSocketClient ${this.instanceId}] Connection timed out. Last activity: ${timeSinceLastPong}ms ago.`);
                 this.handleConnectionTimeout();
            }
        }, 15000);
    }

    private stopPingInterval(): void {
        if (this.pingInterval) {
            console.debug(`[WebSocketClient ${this.instanceId}] Stopping ping interval`);
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    private handleConnectionTimeout(): void {
        console.log(`[WebSocketClient ${this.instanceId}] Connection timeout detected. Closing WebSocket.`);
        this.ws?.close();
    }

    private handleMessage(event: MessageEvent): void {
        // Update activity timestamp on ANY message received
        this.lastPongTime = Date.now();

        if (typeof event.data === 'string') {
            try {
                const message = JSON.parse(event.data) as WebSocketMessage<unknown>;

                if (message.type === WebSocketMessageType.PING) {
                    console.debug(`[WebSocketClient ${this.instanceId}] Received PING, sending PONG`);
                    this.send({ type: WebSocketMessageType.PONG, correlation_id: message.correlation_id });
                    return;
                }

                if (message.type === WebSocketMessageType.PONG || message.type === WebSocketMessageType.INTERNAL_PONG) {
                    console.debug(`[WebSocketClient ${this.instanceId}] Received PONG`);
                    return;
                }

                if (message.type === WebSocketMessageType.VIDEO_FRAME) {
                    const frameData = message.data as { feed_id?: string, frame?: string };
                    
                    // If we have a worker and frame is a string (base64), use the worker
                    if (this.videoWorker && typeof frameData?.frame === 'string') {
                        this.videoWorker.postMessage({ 
                            frameData: frameData.frame,
                            feed_id: frameData.feed_id,
                            originalData: message.data // Pass through other metrics/vehicles
                        });
                    } else {
                        // Fallback to direct notification if no worker
                        this.notifyListeners(message.type, message.data, frameData?.feed_id);
                    }
                    return;
                }

                if (message.type === WebSocketMessageType.AUTH_FAILURE) {
                    console.warn(`[WebSocketClient ${this.instanceId}] Authentication failed:`, message.data);
                    this.currentToken = null;
                    this.tokenManager.refreshToken().catch(e => console.error(`[WebSocketClient ${this.instanceId}] Token refresh failed:`, e));
                }

                if (message.type === WebSocketMessageType.INITIAL_FEED_STATUSES) {
                    console.log(`[WebSocketClient ${this.instanceId}] Received INITIAL_FEED_STATUSES. Feeds:`, (message.data as any)?.feeds?.length);
                }

                if (message.type === WebSocketMessageType.ERROR_NOTIFICATION) {
                    const errorData = message.data as { message?: string, error_code?: string };
                    console.error(`[WebSocketClient ${this.instanceId}] Backend Error:`, errorData);
                    this.notifyError(errorData.error_code || 'backend_error', errorData.message || 'An unexpected error occurred.');
                }

                this.notifyListeners(message.type, message.data);
            } catch (error) {
                console.error(`[WebSocketClient ${this.instanceId}] Error handling WebSocket message:`, error, event.data);
            }
        } else if (event.data instanceof ArrayBuffer) {
            // Handle binary frame data
            this.notifyListeners(WebSocketMessageType.VIDEO_FRAME, { frame: event.data });
        }
    }

    private notifyListeners<T>(type: WebSocketMessageType, data: T, scope?: string): void {
        const typeListeners = this.listeners.get(type);
        if (!typeListeners) return;

        typeListeners.forEach((entry: unknown) => {
            try {
                if (typeof entry === 'object' && entry !== null && 'scope' in entry) {
                    const scopedEntry = entry as ScopedListener;
                    if (!scope || scopedEntry.scope === scope) {
                        scopedEntry.listener(data);
                    }
                } else {
                    (entry as MessageListener<T>)(data);
                }
            } catch (error) {
                console.error(`[WebSocketClient ${this.instanceId}] Error in listener for ${type}:`, error);
            }
        });
    }

    public subscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>, scope?: string): () => void {
        if (!this.listeners.has(messageType)) this.listeners.set(messageType, new Set());
        
        if (scope) {
            const scopedListener: ScopedListener = { scope, listener: listener as unknown as MessageListener<unknown> };
            this.listeners.get(messageType)?.add(scopedListener);
            return () => this.unsubscribe(messageType, listener, scope);
        } else {
            this.listeners.get(messageType)?.add(listener as unknown as MessageListener<unknown>);
            return () => this.unsubscribe(messageType, listener);
        }
    }

    public unsubscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>, scope?: string): void {
        const typeListeners = this.listeners.get(messageType);
        if (!typeListeners) return;

        if (scope) {
            typeListeners.forEach((entry: unknown) => {
                if (typeof entry === 'object' && entry !== null && (entry as ScopedListener).scope === scope && (entry as ScopedListener).listener === listener) {
                    typeListeners.delete(entry as ScopedListener);
                }
            });
        } else {
            typeListeners.delete(listener as MessageListener<unknown>);
        }
    }

    public send(message: WebSocketMessage): void {
        const messageToSend: WebSocketMessage = { ...message, timestamp: message.timestamp ?? Date.now() };
        if (this.isConnected()) {
            this.ws!.send(JSON.stringify(messageToSend));
        } else {
            if (this.messageQueue.length >= this.maxMessageQueueSize) {
                this.messageQueue.shift();
                console.warn(`[WebSocketClient ${this.instanceId}] WebSocket message queue full. Dropping oldest message.`);
            }
            this.messageQueue.push(messageToSend);
        }
    }

    public disconnect(): void {
        console.log(`[WebSocketClient ${this.instanceId}] Disconnecting WebSocket...`);
        this.shouldReconnect = false;
        this.cancelPendingOperations();
        this.ws?.close();
        this.setState(ConnectionState.DISCONNECTED, 'User disconnected');
    }

    public destroy(): void {
        console.log(`[WebSocketClient ${this.instanceId}] Destroying WebSocket client...`);
        if (lastActiveInstanceId === this.instanceId) {
            lastActiveInstanceId = null;
        }
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
