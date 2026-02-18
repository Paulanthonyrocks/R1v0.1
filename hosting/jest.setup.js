import '@testing-library/jest-dom';

// Mocking fetch and related objects
global.Response = jest.fn().mockImplementation(() => ({
  json: () => Promise.resolve({}),
  status: 200,
  ok: true,
}));

global.fetch = jest.fn(() => Promise.resolve(new global.Response()));

// Mocking WebSocket
global.WebSocket = jest.fn().mockImplementation(() => ({
  onopen: jest.fn(),
  onclose: jest.fn(),
  onerror: jest.fn(),
  onmessage: jest.fn(),
  send: jest.fn(),
  close: jest.fn(),
  readyState: 1, // WebSocket.OPEN
  addEventListener: jest.fn(),
  removeEventListener: jest.fn(),
  dispatchEvent: jest.fn(),
}));
