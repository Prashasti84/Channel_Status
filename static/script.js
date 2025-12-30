async function checkChannel() {
    const urlInput = document.getElementById('giphy-url');
    const checkBtn = document.getElementById('check-btn');
    const btnText = document.getElementById('btn-text');
    const btnLoader = document.getElementById('btn-loader');
    const resultsSection = document.getElementById('results-section');
    const errorSection = document.getElementById('error-section');
    
    const url = urlInput.value.trim();
    
    if (!url) {
        showError('Please enter a Giphy URL');
        return;
    }
    
    // Show loading state
    checkBtn.disabled = true;
    btnText.style.display = 'none';
    btnLoader.style.display = 'inline-block';
    resultsSection.style.display = 'none';
    errorSection.style.display = 'none';
    
    try {
        const response = await fetch('/api/check-channel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url: url })
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || 'An error occurred');
        }
        
        // Debug: Log response data
        console.log('API Response:', data);
        
        displayResults(data);
        
    } catch (error) {
        showError(error.message || 'Failed to check channel status. Please try again.');
    } finally {
        // Reset button state
        checkBtn.disabled = false;
        btnText.style.display = 'inline';
        btnLoader.style.display = 'none';
    }
}

function displayResults(data) {
    const resultsSection = document.getElementById('results-section');
    const errorSection = document.getElementById('error-section');
    
    errorSection.style.display = 'none';
    resultsSection.style.display = 'block';
    
    // Update status badge
    updateStatusBadge(data);
    
    // Update channel information
    updateChannelInfo(data);
    
    // Update detection results
    updateDetectionResults(data);
    
    // Update status details (hidden but function called for compatibility)
    updateStatusDetails(data);
}

function updateStatusBadge(data) {
    const statusBadge = document.getElementById('status-badge');
    const statusIcon = document.getElementById('status-icon');
    const statusText = document.getElementById('status-text');
    
    // Remove all status classes
    statusBadge.className = 'status-badge';
    
    let icon = '❓';
    let text = 'Unknown Status';
    let statusClass = 'unknown';
    
    if (data.banned) {
        icon = '🚫';
        text = 'BANNED';
        statusClass = 'banned';
    } else if (data.shadow_banned) {
        icon = '👻';
        text = 'SHADOW BANNED';
        statusClass = 'shadow-banned';
    } else if (data.working) {
        icon = '✅';
        text = 'WORKING';
        statusClass = 'working';
    } else if (data.status === 'not_found') {
        icon = '🔍';
        text = 'NOT FOUND';
        statusClass = 'not-found';
    } else if (data.status === 'error') {
        icon = '⚠️';
        text = 'ERROR';
        statusClass = 'unknown';
    }
    
    statusIcon.textContent = icon;
    statusText.textContent = text;
    statusBadge.classList.add(statusClass);
}

function updateChannelInfo(data) {
    const channelInfo = document.getElementById('channel-info');
    const details = data.details || {};
    
    let html = '';
    let hasAnyData = false;
    
    // Show username (essential)
    if (details.username) {
        html += `<div class="info-item"><strong>Username:</strong><span>${escapeHtml(details.username)}</span></div>`;
        hasAnyData = true;
    } else if (data.channel_id || data.channel_identifier_from_url) {
        const channelId = data.channel_id || data.channel_identifier_from_url;
        html += `<div class="info-item"><strong>Username:</strong><span>${escapeHtml(channelId)}</span></div>`;
        hasAnyData = true;
    }
    
    // Show display name (essential)
    if (details.display_name) {
        html += `<div class="info-item"><strong>Display Name:</strong><span>${escapeHtml(details.display_name)}</span></div>`;
        hasAnyData = true;
    }
    
    // Show profile URL (essential)
    if (details.profile_url) {
        html += `<div class="info-item"><strong>Profile:</strong><span><a href="${escapeHtml(details.profile_url)}" target="_blank">View Profile</a></span></div>`;
        hasAnyData = true;
    } else if (data.channel_id) {
        const profileUrl = `https://giphy.com/${data.channel_id}`;
        html += `<div class="info-item"><strong>Profile:</strong><span><a href="${escapeHtml(profileUrl)}" target="_blank">View Profile</a></span></div>`;
        hasAnyData = true;
    }
    
    // Show total uploads (essential)
    if (details.total_uploads !== undefined) {
        html += `<div class="info-item"><strong>Total Uploads:</strong><span>${details.total_uploads.toLocaleString()}</span></div>`;
        hasAnyData = true;
    } else if (details.recent_gifs_count !== undefined) {
        html += `<div class="info-item"><strong>Total Uploads:</strong><span>${details.recent_gifs_count.toLocaleString()}</span></div>`;
        hasAnyData = true;
    }
    
    // If no data at all, show a message
    if (!hasAnyData) {
        if (data.exists === false) {
            html = '<div class="info-item"><strong>Status:</strong><span>Channel not found or inaccessible</span></div>';
        } else {
            html = '<div class="info-item"><strong>Status:</strong><span>Loading channel information...</span></div>';
        }
    }
    
    channelInfo.innerHTML = html;
}

function updateStatusDetails(data) {
    const statusDetailsCard = document.getElementById('status-details-card');
    
    // Hide this section as it's redundant with the status badge
    if (statusDetailsCard) {
        statusDetailsCard.style.display = 'none';
    }
}

function updateDetectionResults(data) {
    const detectionResults = document.getElementById('detection-results');
    
    let html = '';
    
    // Shadow Banned
    html += `<div class="detection-item ${data.shadow_banned ? 'true' : 'false'}">
        <span class="detection-icon">${data.shadow_banned ? '👻' : '✓'}</span>
        <div>
            <strong>Shadow Banned:</strong> ${data.shadow_banned ? 'Yes' : 'No'}
            ${data.shadow_banned ? '<br><small>Channel exists but content is not visible or accessible</small>' : ''}
        </div>
    </div>`;
    
    // Banned
    html += `<div class="detection-item ${data.banned ? 'true' : 'false'}">
        <span class="detection-icon">${data.banned ? '🚫' : '✓'}</span>
        <div>
            <strong>Banned:</strong> ${data.banned ? 'Yes' : 'No'}
            ${data.banned ? '<br><small>Channel has been explicitly banned</small>' : ''}
        </div>
    </div>`;
    
    // Working
    html += `<div class="detection-item ${data.working ? 'true' : 'false'}">
        <span class="detection-icon">${data.working ? '✅' : '✗'}</span>
        <div>
            <strong>Working:</strong> ${data.working ? 'Yes' : 'No'}
            ${data.working ? '<br><small>Channel is active and accessible</small>' : ''}
        </div>
    </div>`;
    
    // Analysis Reasons (if available)
    if (data.details && data.details.analysis_reasons && data.details.analysis_reasons.length > 0) {
        html += `<div class="detection-item" style="background: linear-gradient(90deg, #1a1f3a 0%, #1e1e3e 100%); border-left-color: #7c8aff; margin-top: 15px;">
            <span class="detection-icon">🔍</span>
            <div>
                <strong style="color: #e8eaff;">Analysis:</strong>
                <ul style="margin: 10px 0 0 0; padding-left: 20px; font-size: 0.95rem; color: #d0d4ff; line-height: 1.6;">
                    ${data.details.analysis_reasons.map(reason => `<li style="margin-bottom: 6px;">${escapeHtml(reason)}</li>`).join('')}
                </ul>
            </div>
        </div>`;
    }
    
    detectionResults.innerHTML = html;
}

function updateAnalyticsInfo(data) {
    const analyticsInfo = document.getElementById('analytics-info');
    const details = data.details || {};
    
    let html = '';
    
    // Only show total views (essential information)
    if (details.total_views !== undefined) {
        const viewsFormatted = details.total_views_formatted || details.total_views.toLocaleString();
        html += `<div class="info-item"><strong>Total Views:</strong><span>${viewsFormatted}</span></div>`;
    } else {
        html = '<div class="info-item"><strong>Total Views:</strong><span>N/A</span></div>';
    }
    
    analyticsInfo.innerHTML = html;
}

function showError(message) {
    const errorSection = document.getElementById('error-section');
    const errorMessage = document.getElementById('error-message');
    const resultsSection = document.getElementById('results-section');
    
    resultsSection.style.display = 'none';
    errorMessage.textContent = message;
    errorSection.style.display = 'block';
}

function updateGifsGallery(data) {
    const gifsGallery = document.getElementById('gifs-gallery');
    const details = data.details || {};
    
    const allGifsToShow = details.all_gifs || details.recent_gifs || [];
    
    if (allGifsToShow.length > 0) {
        let html = `<div style="margin-bottom: 20px; color: #d0d4ff; font-size: 1rem; font-weight: 600;">
            <strong style="color: #e8eaff;">Total GIFs: ${allGifsToShow.length}</strong> | 
            <strong style="color: #e8eaff;">Total Views: ${details.total_views_formatted || formatNumber(details.total_views || 0)}</strong>
        </div>`;
        
        html += `<div class="gifs-grid">`;
        
        allGifsToShow.forEach((gif, index) => {
            const viewsFormatted = (gif.views || 0).toLocaleString();
            const accessibleIcon = gif.accessible !== false ? '✅' : '❌';
            const gifTitle = gif.title ? escapeHtml(gif.title.substring(0, 40)) : `GIF ${index + 1}`;
            const thumbnailUrl = gif.thumbnail_url || gif.preview_url || '';
            
            html += `<div class="gif-card">
                <div class="gif-thumbnail-container">
                    ${thumbnailUrl ? `<img src="${escapeHtml(thumbnailUrl)}" alt="${gifTitle}" class="gif-thumbnail" loading="lazy">` : '<div class="gif-placeholder">📷</div>'}
                    <div class="gif-views-badge">👁️ ${viewsFormatted}</div>
                </div>
                <div class="gif-info">
                    <div class="gif-title">${accessibleIcon} ${gifTitle}</div>
                    ${gif.url ? `<a href="${escapeHtml(gif.url)}" target="_blank" class="gif-link">View on Giphy →</a>` : ''}
                </div>
            </div>`;
        });
        
        html += `</div>`;
        gifsGallery.innerHTML = html;
    } else {
        gifsGallery.innerHTML = '<div class="info-item"><strong>No GIFs found</strong></div>';
    }
}

function formatNumber(num) {
    if (!num) return '0';
    if (num >= 1000000000) {
        return (num / 1000000000).toFixed(1) + 'B';
    } else if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    } else if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Allow Enter key to trigger check
document.getElementById('giphy-url').addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
        checkChannel();
    }
});

