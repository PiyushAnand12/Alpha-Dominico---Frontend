// ──────────────────────────────────────────────────────────
// STEP 1: Your backend address
// Change this to your real Render URL after you deploy.
// During local testing, keep it as localhost.
// ──────────────────────────────────────────────────────────
// To your real Render URL:
const BACKEND = 'https://alpha-dominico-backend.onrender.com';


// ──────────────────────────────────────────────────────────
// STEP 2: Listen for the form submit
// This runs ONCE when the page loads, setting up a listener.
// It waits for the user to click the Subscribe button.
// ──────────────────────────────────────────────────────────
document.getElementById('signup-form')
  .addEventListener('submit', async (e) => {

  // Stop the page from refreshing (default browser behaviour)
  e.preventDefault();


  // ── Find the elements we need to interact with ──────────
  const emailInput = document.getElementById('email-input');
  const submitBtn  = document.getElementById('submit-btn');
  const message    = document.getElementById('message');
  const email      = emailInput.value.trim(); // remove accidental spaces


  // ── Show a "loading" state on the button ────────────────
  submitBtn.disabled   = true;
  submitBtn.textContent = 'Subscribing...';
  message.style.display = 'none'; // hide any old message


  // ── Send the email to your backend ──────────────────────
  try {

    // "fetch" is like making a phone call to your backend.
    // We send the email address packaged as JSON.
    const response = await fetch(`${BACKEND_URL}/subscribe`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ email: email })
    });

    // Read the reply from your backend
    const data = await response.json();


    // ── Show success or error message to the user ───────────
    message.style.display = 'block';

    if (data.success) {
      // 🟢 It worked!
      message.style.color   = '#2f9e44';
      message.textContent   = '✅ ' + data.message;
      emailInput.value      = ''; // clear the email box

    } else {
      // 🔴 Something went wrong (e.g. already subscribed)
      message.style.color   = '#c92a2a';
      message.textContent   = '❌ ' + data.message;
    }

  } catch (err) {
    // 🔴 Network error — couldn't reach the backend at all
    message.style.display = 'block';
    message.style.color   = '#c92a2a';
    message.textContent   = '❌ Network error. Please try again.';
    console.error(err); // log it for debugging

  } finally {
    // Always restore the button — whether it worked or failed
    submitBtn.disabled    = false;
    submitBtn.textContent = 'Get Free Daily Stock Insights';
  }

});