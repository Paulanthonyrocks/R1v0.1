// frontend/lib/websocket.ts

// Make the listener type generic
type MessageListener<T> = (data: T) => void;

// Define a generic structure for WebSocket messages
interface WebSocketMessage<T = unknown> {
  type: string;
  data: T;
}

class WebSocketClient {
  private socket: WebSocket | null = null;
  private url: string;
  private token: string | null = null;
  private pingInterval: number = 30000; // Interval in milliseconds (30 seconds)
  private pingTimer: NodeJS.Timeout | undefined;
  private listeners: Map<string, Set<MessageListener<unknown>>> = new Map();

  // Reconnection properties
  private reconnectAttempts: number = 0;
  private maxReconnectAttempts: number = 10;
  private reconnectDelay: number = 1000; // Initial delay in ms (1 second)
  private maxReconnectDelay: number = 30000; // Max delay in ms (30 seconds)
  private reconnecting: boolean = false;
  private reconnectTimeout: NodeJS.Timeout | undefined;

  constructor(url: string, token: string | null = null) {
    this.url = url;
    this.token = token;
  }

  // --- addEventListener ---
  // Specific public overloads
  public addEventListener(type: 'open', listener: (event: Event) => void, options?: boolean | AddEventListenerOptions): void;
  public addEventListener(type: 'message', listener: (event: MessageEvent) => void, options?: boolean | AddEventListenerOptions): void;
  public addEventListener(type: 'error', listener: (event: Event) => void, options?: boolean | AddEventListenerOptions): void; // Line 20 in error
  public addEventListener(type: 'close', listener: (event: CloseEvent) => void, options?: boolean | AddEventListenerOptions): void;

  // Generic implementation signature. All overloads are checked against this.
  public addEventListener<K extends keyof WebSocketEventMap>(
    type: K,
    listener: (this: WebSocket, ev: WebSocketEventMap[K]) => void, // Corrected 'any' to 'void'
    options?: boolean | AddEventListenerOptions
  ): void { // Line 35 (relatedInformation) was pointing to the start of this block from a previous version
    this.socket?.addEventListener(type, listener, options);
  }


  // --- removeEventListener ---
  // Specific public overloads
  public removeEventListener(type: 'open', listener: (event: Event) => void, options?: boolean | EventListenerOptions): void;
  public removeEventListener(type: 'message', listener: (event: MessageEvent) => void, options?: boolean | EventListenerOptions): void;
  public removeEventListener(type: 'error', listener: (event: Event) => void, options?: boolean | EventListenerOptions): void; // Line 48 in error
  public removeEventListener(type: 'close', listener: (event: CloseEvent) => void, options?: boolean | EventListenerOptions): void;

  // Generic implementation signature. All overloads are checked against this.
  public removeEventListener<K extends keyof WebSocketEventMap>(
    type: K,
    listener: (this: WebSocket, ev: WebSocketEventMap[K]) => void, // Corrected 'any' to 'void'
    options?: boolean | EventListenerOptions
  ): void { // Line 56 (relatedInformation) was pointing to the start of this block from a previous version
    this.socket?.removeEventListener(type, listener, options);
  }

  public connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.socket && this.socket.readyState === WebSocket.OPEN) {
        console.log('WebSocket already open.');
        resolve();
        return;
      }
      if (this.socket && this.socket.readyState === WebSocket.CONNECTING) {
        console.log('WebSocket already connecting.');
        const checkOpen = () => {
          if (this.socket?.readyState === WebSocket.OPEN) {
            resolve();
          } else if (this.socket?.readyState === WebSocket.CLOSED || this.socket?.readyState === WebSocket.CLOSING) {
            reject(new Error('WebSocket connection attempt failed while another was in progress.'));
          } else {
            setTimeout(checkOpen, 100);
          }
        };
        checkOpen();
        return;
      }

      this.reconnecting = false; // Reset reconnection state on explicit connect call
      this.reconnectAttempts = 0; // Reset attempts

      const url = this.token ? `${this.url}?token=${this.token}` : this.url;
      this.socket = new WebSocket(url);

      this.socket.onopen = () => {
        console.log('WebSocket connected');
        this.reconnectAttempts = 0; // Reset on successful connection
        this.reconnecting = false;
        clearTimeout(this.reconnectTimeout); // Clear any pending reconnects
        this.startPinging();
        resolve();
      };

      this.socket.onclose = (event: CloseEvent) => {
        console.log('WebSocket onclose event:', event);
        clearInterval(this.pingTimer);
        this.socket = null; // Clear the socket reference

        if (!event.wasClean && !this.reconnecting) { // Attempt reconnect only if not clean close and not already reconnecting
          console.warn('WebSocket closed unexpectedly. Attempting to reconnect...');
          this.reconnect();
        } else if (event.wasClean) {
          console.log(`WebSocket closed cleanly, code=${event.code}, reason=${event.reason}`);
        }
      };

      this.socket.onerror = (event: Event) => this.handleError(event);

      this.socket.onmessage = (event: MessageEvent) => {
        this.handleIncomingMessage(event.data as string);
      };
    });
  }

  private reconnect(): void {
    if (this.reconnecting) {
      console.log('Already reconnecting, skipping.');
      return;
    }

    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnect attempts reached. Giving up.');
      return;
    }

    this.reconnecting = true;
    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1), this.maxReconnectDelay);
    console.log(`Attempting to reconnect in ${delay / 1000} seconds (attempt ${this.reconnectAttempts})...`);

    this.reconnectTimeout = setTimeout(() => {
      this.connect().then(() => {
        console.log('Reconnection successful!');
      }).catch(error => {
        console.error('Reconnection failed:', error);
        this.reconnecting = false; // Allow next attempt
        this.reconnect(); // Try again
      });
    }, delay);
  }

  public disconnect(): void {
    clearInterval(this.pingTimer);
    clearTimeout(this.reconnectTimeout); // Stop any pending reconnects
    this.reconnecting = false; // Ensure no further reconnects are triggered
    if (this.socket) {
      console.log('Disconnecting WebSocket cleanly.');
      this.socket.close(1000, 'Client initiated disconnect'); // 1000 is normal closure
    }
    this.socket = null;
  }

  

  public sendMessage(type: string, data: unknown): void {
    const message = { type, data };
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    } else {
      console.error('WebSocket is not open. Cannot send message:', message);
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

  private handleIncomingMessage(messageData: string): void {
    try {
      const parsedData: WebSocketMessage = JSON.parse(messageData);
      if (typeof parsedData === 'object' && parsedData !== null && 'type' in parsedData && 'data' in parsedData) {
        const { type: messageType, data: messagePayload } = parsedData;

        if (messageType === 'ping') {
          console.log('Received ping, sending pong');
          this.sendMessage('pong', {});
        } else {
          console.log(`Received message type: ${messageType}`, messagePayload);
          if (this.listeners.has(messageType)) {
            this.listeners.get(messageType)?.forEach((listener: MessageListener<unknown>) => {
              try {
                listener(messagePayload);
              } catch (error) {
                console.error(`Error in listener for message type ${messageType}:`, error);
              }
            });
          }
        }
      } else {
        console.error('Received malformed message or missing type/data property:', parsedData);
      }
    } catch (error) {
      console.error('Error parsing incoming message or malformed JSON:', messageData, error);
    }
  }

  private startPinging(): void {
    clearInterval(this.pingTimer); // Clear existing timer
    this.pingTimer = setInterval(() => {
      if (this.socket && this.socket.readyState === WebSocket.OPEN) {
        this.sendMessage('ping', {});
        console.log('Sent ping');
      } else {
        clearInterval(this.pingTimer);
        console.log('WebSocket not open, stopped pinging.');
      }
    }, this.pingInterval);
  }

  private handleError(event: Event, reject?: (reason?: any) => void): void {
    clearInterval(this.pingTimer);
    console.error('WebSocket error:', event);
    if (this.socket?.readyState !== WebSocket.OPEN) {
      // If error occurs during connection attempt, reject the promise
      if (reject) {
        reject(new Error('WebSocket connection failed.'));
      }
    }
    if (!this.reconnecting) { // Trigger reconnect on error if not already reconnecting
      console.warn('WebSocket error. Attempting to reconnect...');
      this.reconnect();
    }
  }

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

const wsProtocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const clientId = getOrCreateClientId();
const wsUrl = `${wsProtocol}//${process.env.NEXT_PUBLIC_API_URL}/ws/${clientId}`;

// Export the class directly, not a singleton instance
export { WebSocketClient };