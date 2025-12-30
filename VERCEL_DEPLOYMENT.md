# Vercel Deployment Guide

## Prerequisites

1. Vercel account (sign up at https://vercel.com)
2. GitHub repository connected to Vercel

## Environment Variables

Set these in your Vercel project settings (Settings → Environment Variables):

1. **GIPHY_API_KEY** (Required)
   - Get your API key from: https://developers.giphy.com/
   - Example: `L8eXbxrbPETZxlvgXN9kIEzQ55Df04v0`

2. **USE_GIPHY_API** (Optional)
   - Set to `true` or `false`
   - Default: `true`

3. **DB_PATH** (Optional)
   - Database file path
   - Default: `/tmp/giphy_tracking.db` on Vercel

## Important Notes

### Database Limitations
- **SQLite database is ephemeral** on Vercel serverless functions
- Data will be lost when functions restart or redeploy
- For persistent storage, consider using:
  - Vercel Postgres
  - Supabase
  - PlanetScale
  - Other cloud databases

### Proxy Support
- Proxy fetching from webshare.io works on Vercel
- Proxies are fetched per function invocation
- Failed proxies are automatically retried

## Deployment Steps

1. **Push code to GitHub**
   ```bash
   git add .
   git commit -m "Add Vercel configuration"
   git push
   ```

2. **Deploy on Vercel**
   - Go to https://vercel.com
   - Import your GitHub repository
   - Vercel will auto-detect the configuration
   - Add environment variables in project settings
   - Deploy!

3. **Verify Deployment**
   - Visit your Vercel URL
   - Test the `/api/check-channel` endpoint
   - Check Vercel function logs for any errors

## Troubleshooting

### Issue: Database errors
- **Solution**: Database is ephemeral on Vercel. Consider using a cloud database for persistence.

### Issue: Proxy fetching fails
- **Solution**: Check Vercel function logs. Proxy fetching happens on-demand and may fail if webshare.io is unreachable.

### Issue: Channel status not detected correctly
- **Solution**: 
  1. Check that `GIPHY_API_KEY` is set correctly
  2. Verify API key has proper permissions
  3. Check function logs for API errors
  4. Ensure proxy manager is working (check logs)

### Issue: Function timeout
- **Solution**: 
  - Vercel free tier has 10s timeout for Hobby plan
  - Upgrade to Pro for longer timeouts
  - Optimize API calls (reduce number of GIFs checked)

## File Structure

```
.
├── api/
│   └── index.py          # Vercel serverless entry point
├── app.py                # Main Flask application
├── vercel.json           # Vercel configuration
├── requirements.txt      # Python dependencies
└── templates/            # HTML templates
```

## API Endpoints

- `GET /` - Main page
- `POST /api/check-channel` - Check channel status
- `GET /api/get-realtime-views` - Get real-time views

## Support

For issues, check:
1. Vercel function logs (Dashboard → Functions → Logs)
2. Browser console for frontend errors
3. Network tab for API request/response details

