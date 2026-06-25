import { TokenManager } from '../auth/TokenManager';
import { errorNotifier } from '../utils/errorNotifier';
import { decode as msgpackDecode } from '@msgpack/msgpack';

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
    SUBSCRIBE = 'subscribe',
    UNSUBSCRIBE = 'unsubscribe',
    SUBSCRIBE_TO_FEED = 'subscribe_to_feed',
    UNSUBSCRIBE_FROM_FEED = 'unsubscribe_from_feed',
    UPDATE_FEED_CONFIG = 'update_feed_config',
    GET_INITIAL_FEED_STATUSES = 'get_initial_feed_statuses',
    START_FEED = 'start_feed',
    STOP_FEED = 'stop_feed',
    RESTART_FEED = 'restart_feed',
    SNAPSHOT_READY = 'snapshot_ready',
    SET_SIGNAL_PHASE = 'set_signal_phase',
    INTERNAL_PONG = '__internal_pong',
    GET_USER_ROLE = 'get_user_role',
    USER_ROLE = 'user_role'
}

export enum MessagePriority {
    CRITICAL = 0,
    HIGH = 1,
    NORMAL = 2,
    LOW = 3
}

export interface WebSocketMessage<T = unknown> {
    type: WebSocketMessageType;
    data?: T | null;
    client_id?: string;
    correlation_id?: string;
    timestamp?: number;
    priority?: MessagePriority;
}

interface PrioritizedMessage {
    priority: MessagePriority;
    message: WebSocketMessage;
    timestamp: number;
}

export interface ConnectionQuality {
    latency: number;           // Current RTT in ms
    averageLatency: number;    // Moving average
    packetLoss: number;        // Estimated packet loss %
    quality: 'excellent' | 'good' | 'fair' | 'poor';
}

export interface IWebSocketClient {
    activate(): void;
    connect(token: string): Promise<void>;
    disconnect(): void;
    send<T>(data: WebSocketMessage<T>): void;
    subscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>, scope?: string): () => void;
    unsubscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>, scope?: string): void;
    reconnectWithNewToken(token: string): Promise<void>;
    getConnectionQuality(): ConnectionQuality;
    getConnectionState(): string;
    cleanupWorkerResources(feed_id: string): void;
}

enum ConnectionState {
    DISCONNECTED = 'disconnected',
    CONNECTING = 'connecting',
    CONNECTED = 'connected',
    AUTHENTICATED = 'authenticated',
    RECONNECTING = 'reconnecting',
    ERROR = 'error'
}

// Global tracking to ensure only the latest instance in a tab is active
// This prevents infinite loops if multiple instances are created (e.g. during HMR or React Strict Mode)
let lastActiveInstanceId: string | null = null;

export class WebSocketClient implements IWebSocketClient {
    public static DEBUG = false;
    private listeners: Map<WebSocketMessageType, Set<MessageListener<unknown>>> = new Map();
    private scopedListeners: Map<WebSocketMessageType, Map<string, Set<MessageListener<unknown>>>> = new Map();
    private ws: WebSocket | null = null;
    private reconnectAttempts = 0;
    private maxReconnectAttempts = 10;
    private reconnectDelay = 500;
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
    private resolveConnection: (() => void) | null = null;
    private rejectConnection: ((reason: any) => void) | null = null;
    private currentToken: string | null = null;
    private authenticated = false;
    private videoWorker: Worker | null = null;
    private requiresClientId: boolean;
    private clientId: string | null = null;
    private latencyHistory: number[] = [];
    private cleanupCounter = 0;
    private maxLatencyHistory = 10;
    private sentPingTimestamps: Map<string, number> = new Map();
    private qualityListeners: Set<(quality: ConnectionQuality) => void> = new Set();
    private networkChangeHandler: (() => void) | null = null;
    private networkOfflineHandler: (() => void) | null = null;

    private pendingFrames: Map<string, number> = new Map();
    private readonly MAX_PENDING_FRAMES = 5;
    
    // Frame buffer to handle subscription race conditions
    private frameBufferByFeed: Map<string, Array<any>> = new Map();
    private readonly MAX_BUFFERED_FRAMES_PER_FEED = 3;
    private bufferExpirationTimers: Map<string, NodeJS.Timeout> = new Map();
    private activeListeningFeeds: Set<string> = new Set();

    private workerUrl: string;

    constructor(baseUrl: string, requiresClientId = true, workerUrl = '/workers/video-worker.js') {
        this.url = baseUrl;
        this.requiresClientId = requiresClientId;
        this.workerUrl = workerUrl;
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
            this.videoWorker = new Worker(this.workerUrl);
            this.videoWorker.onmessage = (e) => {
                const feedId = e.data.feed_id;
                
                if (e.data.error) {
                    console.error(`[WebSocketClient ${this.instanceId}] Worker: Frame decoding failed`, e.data.error);
                    // Decrement pending frames counter even on error to prevent permanent blocking
                    if (feedId) {
                        const pending = this.pendingFrames.get(feedId) ?? 1;
                        if (pending > 1) {
                            this.pendingFrames.set(feedId, pending - 1);
                        } else {
                            this.pendingFrames.delete(feedId);
                        }
                    }
                } else {
                    console.log(`[WebSocketClient] Worker returned frame for feed: ${feedId}`);
                    
                    // Decrement pending frames counter
                    if (feedId) {
                        const pending = this.pendingFrames.get(feedId) ?? 1;
                        if (pending > 1) {
                            this.pendingFrames.set(feedId, pending - 1);
                        } else {
                            this.pendingFrames.delete(feedId);
                        }
                    }
                    
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

        // Setup network change detection
        if (!this.networkChangeHandler) {
            this.networkChangeHandler = () => {
                console.log(`[WebSocketClient ${this.instanceId}] Network back online, reconnecting...`);

                if (!this.isConnected() && this.isInstanceActive()) {
                    this.reconnectAttempts = 0; // Reset attempts on network change
                    this.connect(this.currentToken).catch(e => {
                        console.error(`[WebSocketClient ${this.instanceId}] Reconnect after network change failed:`, e);
                    });
                }
            };

            this.networkOfflineHandler = () => {
                console.log(`[WebSocketClient ${this.instanceId}] Network offline detected`);
                this.setState(ConnectionState.DISCONNECTED, 'Network offline');
            };

            window.addEventListener('online', this.networkChangeHandler);
            window.addEventListener('offline', this.networkOfflineHandler);
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

    private calculateConnectionQuality(): ConnectionQuality {
        const avgLatency = this.latencyHistory.length > 0
            ? this.latencyHistory.reduce((a, b) => a + b, 0) / this.latencyHistory.length
            : 0;

        let quality: ConnectionQuality['quality'];
        if (avgLatency < 50) quality = 'excellent';
        else if (avgLatency < 150) quality = 'good';
        else if (avgLatency < 300) quality = 'fair';
        else quality = 'poor';

        return {
            latency: this.latencyHistory[this.latencyHistory.length - 1] || 0,
            averageLatency: avgLatency,
            packetLoss: 0, // Can be enhanced to track missed pongs
            quality
        };
    }

    private notifyQualityListeners(quality: ConnectionQuality) {
        this.qualityListeners.forEach(listener => {
            try {
                listener(quality);
            } catch (error) {
                console.error(`[WebSocketClient ${this.instanceId}] Error in quality listener:`, error);
            }
        });
    }

    public onQualityChange(listener: (quality: ConnectionQuality) => void): () => void {
        this.qualityListeners.add(listener);
        // Immediately notify with current quality
        try {
            listener(this.calculateConnectionQuality());
        } catch (error) {
            console.error(`[WebSocketClient ${this.instanceId}] Error in initial quality notification:`, error);
        }
        return () => this.qualityListeners.delete(listener);
    }

    public getConnectionQuality(): ConnectionQuality {
        return this.calculateConnectionQuality();
    }

    public getConnectionState(): string {
        return this.connectionState;
    }

    public onStatusChange(listener: (status: string, message?: string) => void): () => void {
        this.statusListeners.add(listener);
        // Immediately notify the listener of the current state
        try {
            listener(this.connectionState);
        } catch (error) {
            console.error(`[WebSocketClient ${this.instanceId}] Error in initial status notification:`, error);
        }
        return () => this.statusListeners.delete(listener);
    }

    private tokenRefreshTimeout: NodeJS.Timeout | null = null;

    private async handleTokenRefresh(token: string): Promise<void> {
        if (this.tokenRefreshTimeout) {
            clearTimeout(this.tokenRefreshTimeout);
        }

        this.tokenRefreshTimeout = setTimeout(async () => {
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
            this.tokenRefreshTimeout = null;
        }, 200); // 200ms debounce
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
            this.setState(ConnectionState.DISCONNECTED, 'Waiting for authentication token');
            this.notifyError('auth_error', 'No authentication token available.');
            return;
        }

        this.currentToken = token;

        return new Promise<void>((resolve, reject) => {
            try {
                this.resolveConnection = resolve;
                this.rejectConnection = reject;

                if (!this.isInstanceActive()) {
                    reject(new Error('Instance became dormant during connection setup'));
                    return;
                }

                const clientId = this.clientId;
                const url = new URL(this.url);

                if (clientId) {
                    // Ensure the path starts with /api/v1/ws and ends with the clientId
                    const pathParts = ['api', 'v1', 'ws', clientId].filter(Boolean);
                    url.pathname = '/' + pathParts.join('/');
                }

                console.log(`[WebSocketClient ${this.instanceId}] Attempting to connect to WebSocket:`, url.toString());

                this.ws = new WebSocket(url.toString());
                this.ws.binaryType = 'arraybuffer';

                const connectionTimeout = setTimeout(() => {
                    if (this.ws?.readyState === WebSocket.CONNECTING) {
                        this.ws.close();
                        reject(new Error('Connection timeout'));
                    }
                }, 30000);

                this.ws.onopen = () => {
                    clearTimeout(connectionTimeout);

                    if (!this.shouldReconnect || !this.isInstanceActive()) {
                        console.debug(`[WebSocketClient ${this.instanceId}] Connection opened but instance is dormant or destroying. Closing.`);
                        this.ws?.close();
                        return;
                    }

                    console.log(`[WebSocketClient ${this.instanceId}] WebSocket opened. ${clientId ? `Client ID: ${clientId}` : ''}`);
                    
                    // Authenticate upon connection with a small delay to ensure the socket is settled
                    if (this.currentToken) {
                        setTimeout(() => {
                            this.send({
                                type: WebSocketMessageType.AUTHENTICATE,
                                data: { token: this.currentToken }
                            });
                            console.log(`[WebSocketClient ${this.instanceId}] Sent initial AUTHENTICATE message`);
                        }, 50);
                    } else {
                        console.warn(`[WebSocketClient ${this.instanceId}] No token available for initial authentication`);
                    }

                    this.setState(ConnectionState.CONNECTED, 'Connection established');
                    this.reconnectAttempts = 0;
                    this.reconnectDelay = 1000;
                    this.startPingInterval();
                    // Do not flush queue here; wait for AUTH_SUCCESS
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
                    console.error(`[WebSocketClient ${this.instanceId}] WebSocket error occurred. (Note: WebSocket onerror events typically do not contain detailed error information for security reasons)`, {
                        readyState: this.ws?.readyState,
                        url: this.ws?.url,
                        event: event
                    });
                    const errorMessage = 'WebSocket error occurred';
                    this.setState(ConnectionState.ERROR, errorMessage);
                    this.notifyError('connection_error', errorMessage);
                    if (this.connectionState === ConnectionState.CONNECTING) reject(new Error(errorMessage));
                };

                this.ws.onmessage = (event) => {
                    // onmessage must NEVER throw — if it does, browsers stop
                    // dispatching further messages on this socket (silent
                    // pipeline death). Downstream parsers have historically
                    // crashed on circular AUTH_FAILURE payloads.
                    try {
                        this.handleMessage(event);
                    } catch (e) {
                        console.error(
                            `[WebSocketClient ${this.instanceId}] handleMessage crashed:`,
                            e
                        );
                        // Drop the socket so the watchdog can reconnect
                        // instead of us silently missing every subsequent frame.
                        try {
                            this.ws?.close();
                        } catch (_) {
                            // best-effort
                        }
                    }
                };

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
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
                console.debug(`[WebSocketClient ${this.instanceId}] Ping interval skipping: socket not open.`);
                return;
            }

            // Generate correlation ID for RTT tracking
            const pingId = crypto.randomUUID();
            const now = Date.now();
            this.sentPingTimestamps.set(pingId, now);

            // Sending a PING to the server, expecting a PONG back
            console.debug(`[WebSocketClient ${this.instanceId}] Sending PING with correlation_id: ${pingId}`);
            this.send({
                type: WebSocketMessageType.PING,
                timestamp: now,
                correlation_id: pingId
            });

            // Cleanup old ping timestamps (older than 2 minutes)
            for (const [id, timestamp] of this.sentPingTimestamps.entries()) {
                if (now - timestamp > 120000) {
                    this.sentPingTimestamps.delete(id);
                }
            }

            // If no pong is received within 60 seconds, consider connection timed out
            const timeSinceLastPong = Date.now() - this.lastPongTime;
            if (timeSinceLastPong > 60000) {
                console.warn(`[WebSocketClient ${this.instanceId}] Connection timed out. Last activity: ${timeSinceLastPong}ms ago.`);
                this.handleConnectionTimeout();
            }
        }, 10000);
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
                    this.send({
                        type: WebSocketMessageType.PONG,
                        correlation_id: message.correlation_id,
                        timestamp: message.timestamp // Echo back the server's timestamp
                    });
                    return;
                }

                if (message.type === WebSocketMessageType.PONG || message.type === WebSocketMessageType.INTERNAL_PONG) {
                    console.debug(`[WebSocketClient ${this.instanceId}] Received PONG`);

                    // Calculate RTT if we have a correlation_id
                    if (message.correlation_id) {
                        const sentTime = this.sentPingTimestamps.get(message.correlation_id);
                        if (sentTime) {
                            const rtt = Date.now() - sentTime;
                            this.latencyHistory.push(rtt);

                            // Keep only recent history
                            if (this.latencyHistory.length > this.maxLatencyHistory) {
                                this.latencyHistory.shift();
                            }

                            this.sentPingTimestamps.delete(message.correlation_id);

                            // Calculate and notify quality
                            const quality = this.calculateConnectionQuality();
                            console.debug(`[WebSocketClient ${this.instanceId}] RTT: ${rtt}ms, Quality: ${quality.quality}`);
                            this.notifyQualityListeners(quality);
                        }
                    }
                    return;
                }

                if (message.type === WebSocketMessageType.VIDEO_FRAME) {
                    const frameData = message.data as { feed_id?: string, frame?: string };
                    const f_id = frameData?.feed_id;

                    if (!f_id) return;

                    // Frame dropping mechanism to prevent worker congestion
                    const pending = this.pendingFrames.get(f_id) ?? 0;
                    if (pending >= this.MAX_PENDING_FRAMES) {
                        return; // Drop frame
                    }

                    // CRITICAL FIX: always route VIDEO_FRAME through exactly ONE path.
                    // Previously, if the worker existed but `frameData.frame` was not
                    // a base64 string (e.g. an ImageBitmap already, or an ArrayBuffer
                    // payload), the JSON branch above fell through and was dropped —
                    // while the binary branch kept streaming the same feed. The user
                    // observed a flood of "DROPPING stale frame" warnings exactly
                    // because the two paths interleaved. We now route both binary and
                    // JSON into the worker when available; falling back to direct
                    // notification only when the worker has been terminated.
                    if (this.videoWorker) {
                        this.pendingFrames.set(f_id, pending + 1);
                        this.videoWorker.postMessage({
                            frameData: typeof frameData.frame === 'string' ? frameData.frame : null,
                            feed_id: f_id,
                            originalData: message.data
                        });
                    } else {
                        this.notifyListeners(message.type, message.data, f_id);
                    }
                    return;
                }

                if (message.type === WebSocketMessageType.AUTH_SUCCESS) {
                    console.log(`[WebSocketClient ${this.instanceId}] Authentication successful.`);
                    this.authenticated = true;
                    this.setState(ConnectionState.AUTHENTICATED, 'Authenticated');
                    this.reconnectAttempts = 0;
                    this.reconnectDelay = 1000;
                    this.flushMessageQueue();
                    
                    if (this.resolveConnection) {
                        this.resolveConnection();
                        this.resolveConnection = null;
                    }
                    return;
                }

                if (message.type === WebSocketMessageType.AUTH_FAILURE) {
                    console.warn(`[WebSocketClient ${this.instanceId}] Authentication failed:`, message.data);
                    this.authenticated = false;
                    this.currentToken = null;

                    if (this.rejectConnection) {
                        // JSON.stringify throws on circular structures and BigInts.
                        // Backend AUTH_FAILURE data is expected to be a small dict,
                        // but in practice it has carried server tracebacks, gql
                        // errors, and request objects that can stringify-throw.
                        // Truncate to a 500-char summary so the rejection value
                        // never blows up the WebSocket onmessage path itself.
                        let detail: string;
                        try {
                            detail = JSON.stringify(message.data) ?? '';
                        } catch (_) {
                            try {
                                detail = String(message.data);
                            } catch (_) {
                                detail = '<unserializable>';
                            }
                        }
                        if (detail.length > 500) {
                            detail = detail.slice(0, 500) + '…';
                        }
                        try {
                            this.rejectConnection(new Error(`Authentication failed: ${detail}`));
                        } catch (rejectionErr) {
                            console.error(
                                `[WebSocketClient ${this.instanceId}] rejectConnection threw:`,
                                rejectionErr
                            );
                        }
                        this.rejectConnection = null;
                    }

                    // Attempt to refresh token and reconnect
                    setTimeout(() => {
                        if (this.isInstanceActive() && this.shouldReconnect) {
                            console.log(`[WebSocketClient ${this.instanceId}] Attempting token refresh after AUTH_FAILURE...`);
                            this.tokenManager.refreshToken()
                                .then(token => {
                                    if (token) this.reconnectWithNewToken(token);
                                })
                                .catch(e => console.error(`[WebSocketClient ${this.instanceId}] Token refresh failed:`, e));
                        }
                    }, 5000); // 5s delay before retry
                    return;
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
            try {
                if (this.videoWorker) {
                    // Decode header to get feed_id for congestion control
                    const decoded: any = msgpackDecode(new Uint8Array(event.data));
                    const f_id = decoded.f;
                    
                    if (!f_id) {
                        console.warn(`[WebSocketClient ${this.instanceId}] Binary frame missing feed_id, dropping`);
                        return;
                    }

                    if (f_id) {
                        const pending = this.pendingFrames.get(f_id) ?? 0;
                        if (pending >= this.MAX_PENDING_FRAMES) {
                            return; // Drop frame
                        }
                        this.pendingFrames.set(f_id, pending + 1);
                    }

                    this.videoWorker.postMessage({
                        rawBinary: event.data,
                    }, [event.data]);
                } else {
                    console.warn(`[WebSocketClient ${this.instanceId}] No video worker available for binary frame.`);
                }
            } catch (error) {
                console.error(`[WebSocketClient ${this.instanceId}] Error posting binary data to worker:`, error);
            }
        }
    }

    private notifyListeners<T>(type: WebSocketMessageType, data: T, scope?: string): void {
        // Special handling for high-frequency video frames to ensure strict routing and performance
        if (type === WebSocketMessageType.VIDEO_FRAME) {
            if (!scope) {
                console.debug(`[WebSocketClient ${this.instanceId}] DROPPING unscoped VIDEO_FRAME: scope is missing`);
                return;
            }

            const scopedMap = this.scopedListeners.get(type);
            if (!scopedMap) {
                // Buffer the frame for late subscribers (handles race condition)
                this.bufferFrameForFeed(scope, data);
                return;
            }

            const listenersSet = scopedMap.get(scope);
            if (!listenersSet || listenersSet.size === 0) {
                // Buffer the frame for late subscribers (handles race condition)
                this.bufferFrameForFeed(scope, data);
                return;
            }

            // Track listening feeds for cleanup
            this.activeListeningFeeds.add(scope);
            
            listenersSet.forEach(listener => {
                try {
                    listener(data);
                } catch (error) {
                    console.error(`[WebSocketClient ${this.instanceId}] Error in VIDEO_FRAME scoped listener for ${scope}:`, error);
                }
            });
            return; // Exit early for video frames to prevent any fall-through
        }

        // Standard routing for all other message types
        if (scope) {
            const scopedMap = this.scopedListeners.get(type);
            if (scopedMap) {
                const listenersSet = scopedMap.get(scope);
                if (listenersSet) {
                    console.debug(`[WebSocketClient ${this.instanceId}] Routing ${type} to scope ${scope} (${listenersSet.size} listeners)`);
                    listenersSet.forEach(listener => {
                        try {
                            listener(data);
                        } catch (error) {
                            console.error(`[WebSocketClient ${this.instanceId}] Error in scoped listener for ${type} (scope: ${scope}):`, error);
                        }
                    });
                } else {
                    console.debug(`[WebSocketClient ${this.instanceId}] No listeners for scope ${scope} of type ${type}`);
                }
            }
        } else {
            console.debug(`[WebSocketClient ${this.instanceId}] No scope provided for ${type} message`);
        }

        // Handle Unscoped Listeners (except for VIDEO_FRAME, handled above)
        const unscopedListeners = this.listeners.get(type);
        if (unscopedListeners) {
            console.debug(`[WebSocketClient ${this.instanceId}] Notifying ${unscopedListeners.size} unscoped listeners for ${type}`);
            unscopedListeners.forEach((listener) => {
                try {
                    (listener as MessageListener<T>)(data);
                } catch (error) {
                    console.error(`[WebSocketClient ${this.instanceId}] Error in unscoped listener for ${type}:`, error);
                }
            });
        }
    }

    public subscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>, scope?: string): () => void {
        if (scope) {
            console.log(`[WebSocketClient ${this.instanceId}] SUBSCRIBING to ${messageType} with scope: ${scope}`);
            if (!this.scopedListeners.has(messageType)) {
                this.scopedListeners.set(messageType, new Map());
            }
            const scopedMap = this.scopedListeners.get(messageType)!;
            if (!scopedMap.has(scope)) {
                scopedMap.set(scope, new Set());
            }
            scopedMap.get(scope)!.add(listener as unknown as MessageListener<unknown>);
            
            // If subscribing to VIDEO_FRAME, flush any buffered frames
            if (messageType === WebSocketMessageType.VIDEO_FRAME) {
                this.activeListeningFeeds.add(scope);
                this.flushBufferedFramesForFeed(scope);
            }
        } else {
            if (typeof window !== 'undefined' && (window as any).__WS_DEBUG_SUBSCRIBES__) {
                console.log(`[WebSocketClient ${this.instanceId}] SUBSCRIBING to ${messageType} (unscoped)`);
            }
            if (!this.listeners.has(messageType)) {
                this.listeners.set(messageType, new Set());
            }
            this.listeners.get(messageType)!.add(listener as unknown as MessageListener<unknown>);
        }

        return () => this.unsubscribe(messageType, listener, scope);
    }

    public unsubscribe<T>(messageType: WebSocketMessageType, listener: MessageListener<T>, scope?: string): void {
        if (scope) {
            console.log(`[WebSocketClient ${this.instanceId}] UNSUBSCRIBING from ${messageType} with scope: ${scope}`);
            const scopedMap = this.scopedListeners.get(messageType);
            if (scopedMap) {
                const listenersSet = scopedMap.get(scope);
                if (listenersSet) {
                    listenersSet.delete(listener as unknown as MessageListener<unknown>);
                    if (listenersSet.size === 0) {
                        scopedMap.delete(scope);
                        // Remove from active listening feeds when no more listeners
                        if (messageType === WebSocketMessageType.VIDEO_FRAME) {
                            this.activeListeningFeeds.delete(scope);
                        }
                    }
                }
            }
        } else {
            if (typeof window !== 'undefined' && (window as any).__WS_DEBUG_SUBSCRIBES__) {
                console.log(`[WebSocketClient ${this.instanceId}] UNSUBSCRIBING from ${messageType} (unscoped)`);
            }
            const typeListeners = this.listeners.get(messageType);
            if (typeListeners) {
                typeListeners.delete(listener as any);
            }
        }
    }

    public sendCommand(type: string, feed_id: string, data: any): void {
        this.send({
            type: WebSocketMessageType.GENERAL_NOTIFICATION, // Or add a new type if needed
            data: {
                type: type,
                feed_id: feed_id,
                data: data
            }
        });
    }

    public send(message: WebSocketMessage): void {
        const messageToSend: WebSocketMessage = { ...message, timestamp: message.timestamp ?? Date.now() };
        
        // Special case: allow AUTHENTICATE messages regardless of state
        if (message.type === WebSocketMessageType.AUTHENTICATE) {
            if (this.isConnected()) {
                this.ws!.send(JSON.stringify(messageToSend));
            } else {
                // Queue authentication if disconnected (though usually we connect first)
                this.addToQueue(messageToSend);
            }
            return;
        }

        if (this.isConnected() && this.authenticated) {
            this.ws!.send(JSON.stringify(messageToSend));
        } else {
            // If disconnected or not yet authenticated, queue high-priority messages.
            const priority = message.priority ?? MessagePriority.NORMAL;
            if (priority === MessagePriority.LOW) {
                if (WebSocketClient.DEBUG) console.debug(`[WebSocketClient ${this.instanceId}] Socket not ready/auth'd. Dropping LOW priority message.`);
                return;
            }

            this.addToQueue(messageToSend);
        }
    }

    private addToQueue(message: WebSocketMessage): void {
        this.messageQueue.push(message);
        
        // Sort queue by priority (lower value = higher priority) and then by timestamp (FIFO)
        this.messageQueue.sort((a, b) => {
            const priorityA = a.priority ?? MessagePriority.NORMAL;
            const priorityB = b.priority ?? MessagePriority.NORMAL;
            if (priorityA !== priorityB) {
                return priorityA - priorityB;
            }
            return (a.timestamp ?? 0) - (b.timestamp ?? 0);
        });

        // If queue is full, drop the LOWEST priority message (which is now at the end)
        if (this.messageQueue.length > this.maxMessageQueueSize) {
            this.messageQueue.pop();
            if (WebSocketClient.DEBUG) console.warn(`[WebSocketClient ${this.instanceId}] WebSocket message queue full. Dropped lowest priority message.`);
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
        this.qualityListeners.clear();
        this.scopedListeners.clear();
        this.unsubscribeTokenRefresh?.();
        this.videoWorker?.terminate();
        this.messageQueue.length = 0;

        // Cleanup network event listeners
        if (typeof window !== 'undefined') {
            if (this.networkChangeHandler) {
                window.removeEventListener('online', this.networkChangeHandler);
                this.networkChangeHandler = null;
            }
            if (this.networkOfflineHandler) {
                window.removeEventListener('offline', this.networkOfflineHandler);
                this.networkOfflineHandler = null;
            }
        }
    }

    public isConnected(): boolean {
        return this.ws !== null && this.ws.readyState === WebSocket.OPEN && 
               (this.connectionState === ConnectionState.CONNECTED || this.connectionState === ConnectionState.AUTHENTICATED);
    }

    public cleanupWorkerResources(feed_id: string): void {
        this.cleanupCounter++;
        console.log(`[WebSocketClient ${this.instanceId}] Cleanup #${this.cleanupCounter} for feed ${feed_id}`);

        if (this.videoWorker) {
            this.videoWorker.postMessage({
                command: 'CLEANUP_FEED',
                feed_id: feed_id
            });
            console.log(`[WebSocketClient ${this.instanceId}] Cleanup command sent for feed ${feed_id}`);
        }
        // Clear any buffered frames for this feed
        this.frameBufferByFeed.delete(feed_id);
        const timer = this.bufferExpirationTimers.get(feed_id);
        if (timer) {
            clearTimeout(timer);
            this.bufferExpirationTimers.delete(feed_id);
        }
        // Clear pending frames counter
        this.pendingFrames.delete(feed_id);
        console.log(`[WebSocketClient ${this.instanceId}] Pending frames counter cleared for feed ${feed_id}`);
        this.activeListeningFeeds.delete(feed_id);
    }
    
    /**
     * Buffer a frame for a feed that has no listeners yet.
     * This handles the race condition where frames arrive before subscriptions are established.
     */
    private bufferFrameForFeed(feedId: string, frameData: any): void {
        let buffer = this.frameBufferByFeed.get(feedId);
        if (!buffer) {
            buffer = [];
            this.frameBufferByFeed.set(feedId, buffer);
        }
        
        // Only buffer if we don't have a listener (avoid memory bloat)
        if (!this.activeListeningFeeds.has(feedId)) {
            buffer.push(frameData);
            // Keep only the latest N frames to prevent memory issues
            while (buffer.length > this.MAX_BUFFERED_FRAMES_PER_FEED) {
                buffer.shift();
            }
            
            // Set expiration timer to clear old buffered frames (10 seconds)
            if (!this.bufferExpirationTimers.has(feedId)) {
                const timer = setTimeout(() => {
                    this.frameBufferByFeed.delete(feedId);
                    this.bufferExpirationTimers.delete(feedId);
                }, 10000);
                this.bufferExpirationTimers.set(feedId, timer);
            }
        }
    }
    
    /**
     * Flush any buffered frames for a feed to the newly-registered listener.
     * Called when a subscription is established for a feed.
     */
    private flushBufferedFramesForFeed(feedId: string): void {
        const buffer = this.frameBufferByFeed.get(feedId);
        if (!buffer || buffer.length === 0) return;
        
        const scopedMap = this.scopedListeners.get(WebSocketMessageType.VIDEO_FRAME);
        if (!scopedMap) return;
        
        const listenersSet = scopedMap.get(feedId);
        if (!listenersSet || listenersSet.size === 0) return;
        
        console.log(`[WebSocketClient ${this.instanceId}] Flushing ${buffer.length} buffered frames for ${feedId}`);
        
        // Deliver buffered frames (just the latest one to avoid flooding)
        const latestFrame = buffer[buffer.length - 1];
        try {
            listenersSet.forEach(listener => {
                try {
                    listener(latestFrame);
                } catch (error) {
                    console.error(`[WebSocketClient ${this.instanceId}] Error flushing buffered frame:`, error);
                }
            });
        } finally {
            // Clear the buffer after flushing
            this.frameBufferByFeed.delete(feedId);
            const timer = this.bufferExpirationTimers.get(feedId);
            if (timer) {
                clearTimeout(timer);
                this.bufferExpirationTimers.delete(feedId);
            }
        }
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
