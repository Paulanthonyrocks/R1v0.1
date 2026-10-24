import { APIClient } from '../api/APIClient';
import { getApiBrowserBaseURL } from '../api/backendBaseUrl';

// Wire contract (backend/app/routers/video.py):
// - GET  /api/v1/video/processed-videos?stream_id=&limit=&offset= -> ProcessedVideo[]
// - GET  /api/v1/video/processed-videos/{id}/download -> FileResponse video/mp4
// - POST /api/v1/video/record/start  body {stream_id, output_filename, frame_rate}
//        -> {message, output_path}; output_filename is server-sanitized to a bare
//        name (path traversal guard) and .mp4-suffixed automatically.
// - POST /api/v1/video/record/stop?stream_id=... (stream_id is a QUERY param, not body)
// NOTE: recording is opt-in server-side (video_output.enabled); a 503 here means
// the pipeline is disabled in config, not a wiring bug.
export interface ProcessedVideo {
    id: number;
    stream_id: string;
    file_path: string;
    start_time: string;
    end_time: string;
    duration: number;
}

export interface RecordStartResponse {
    message: string;
    output_path: string;
}

export interface RecordStopResponse {
    message: string;
}

export class VideoService {
    private static instance: VideoService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
    }

    public static getInstance(): VideoService {
        if (!VideoService.instance) {
            VideoService.instance = new VideoService();
        }
        return VideoService.instance;
    }

    /** Metadata list of processed recordings; filterable per stream. */
    async listProcessedVideos(params: { stream_id?: string; limit?: number; offset?: number } = {}): Promise<ProcessedVideo[]> {
        const query: Record<string, string> = {};
        if (params.stream_id) query.stream_id = params.stream_id;
        if (params.limit !== undefined) query.limit = String(params.limit);
        if (params.offset !== undefined) query.offset = String(params.offset);
        return this.apiClient.get<ProcessedVideo[]>('/api/v1/video/processed-videos', query);
    }

    /**
     * Authenticated binary download of a processed recording. Returns the Blob;
     * the caller names the file. (Raw fetch inside APIClient.getBlob is the
     * sanctioned binary path — it shares Authorization + tunnel handling.)
     */
    async downloadVideo(videoId: number): Promise<Blob> {
        return this.apiClient.getBlob(`/api/v1/video/processed-videos/${videoId}/download`);
    }

    /** Starts recording a stream. output_filename is auto-named when omitted. */
    async startRecording(streamId: string, outputFilename?: string, frameRate: number = 10.0): Promise<RecordStartResponse> {
        const name = outputFilename ?? `${streamId.replace(/[^\w-]/g, '_')}_${Date.now()}.mp4`;
        return this.apiClient.post<RecordStartResponse, { stream_id: string; output_filename: string; frame_rate: number }>(
            '/api/v1/video/record/start',
            { stream_id: streamId, output_filename: name, frame_rate: frameRate }
        );
    }

    /** Stops recording a stream. stream_id rides the QUERY string (backend contract). */
    async stopRecording(streamId: string): Promise<RecordStopResponse> {
        return this.apiClient.post<RecordStopResponse, unknown>(
            `/api/v1/video/record/stop?stream_id=${encodeURIComponent(streamId)}`
        );
    }
}

export const videoService = VideoService.getInstance();
