// frontend/lib/websocket.ts

// Make the listener type generic
type MessageListener<T> = (data: T) => void;

class WebSocketClient {
  private socket: WebSocket | null = null;
  private url: string;
  private pingInterval: number = 30000; // Interval in milliseconds (30 seconds)
  private pingTimer: NodeJS.Timeout | undefined;
  // Use MessageListener<unknown> for the internal map
  private listeners: Map<string, Set<MessageListener<unknown>>> = new Map();

  constructor(url: string) {
    this.url = url;
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
        resolve();
        return;
      }
      if (this.socket && this.socket.readyState === WebSocket.CONNECTING) {
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

      this.socket = new WebSocket(this.url);

      // Line 87 (eslint no-unused-vars for 'event')
      this.socket.onopen = () => { // Changed 'event' to '_event' as it's not used
        console.log('WebSocket connected');
        this.startPinging();
        resolve();
      };

      this.socket.onclose = (event: CloseEvent) => {
        console.log('WebSocket onclose event:', event);
        clearInterval(this.pingTimer);
        if (event.wasClean) {
          console.log(`WebSocket closed cleanly, code=${event.code}, reason=${event.reason}`);
        } else {
          console.error('WebSocket connection died:', event);
        }
        this.socket = null;
      };

      this.socket.onerror = (event: Event) => {
        clearInterval(this.pingTimer);
        console.error('WebSocket error:', event);
        if (this.socket?.readyState !== WebSocket.OPEN) {
             reject(event); // Reject only if the connection wasn't established
        }
      };

      this.socket.onmessage = (event: MessageEvent) => {
        this.handleIncomingMessage(event.data as string); // Assuming event.data is string
      };
    });
  }

  public disconnect(): void {
    clearInterval(this.pingTimer);
    if (this.socket) {
      this.socket.close();
    }
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
      const parsedData = JSON.parse(messageData);
      if (typeof parsedData === 'object' && parsedData !== null && 'type' in parsedData) {
        const messageType = parsedData.type as string;
        const messagePayload: unknown = parsedData.data;

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
        console.error('Received malformed message:', parsedData);
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

}

const wsProtocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${wsProtocol}//${process.env.NEXT_PUBLIC_API_URL}/ws`;
const ws = new WebSocketClient(wsUrl);

export { ws, WebSocketClient as default };