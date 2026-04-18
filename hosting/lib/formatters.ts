export function formatFeedName(name?: string, id?: string): string {
  if (name) return name;
  if (!id) return "Unknown Feed";

  return id
    .replace(/[-_]/g, ' ') // Replace - and _ with space
    .split(' ')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

export function formatFeedSource(source?: string): string {
  if (!source) return 'N/A';
  
  // Remove protocol if present (rtsp://, http://, etc.)
  const cleanSource = source.replace(/^[a-z]+:\/\//i, '');
  
  // If it's a long URL/path, just show the first part or limit length
  if (cleanSource.length > 30) {
    return cleanSource.substring(0, 27) + '...';
  }
  
  return cleanSource;
}
