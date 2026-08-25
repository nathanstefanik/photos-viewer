/**
 * Access-code gate form: normalizes the entered code and redirects to /t/{code}.
 */
if (new URLSearchParams(location.search).get('error') === 'invalid') {
  document.getElementById('err').hidden = false;
}

document.getElementById('form').addEventListener('submit', (e) => {
  e.preventDefault();
  const code = document.getElementById('code').value.replace(/[^a-zA-Z0-9]/g, '');
  if (code.length !== 6) {
    document.getElementById('err').hidden = false;
    return;
  }
  location.href = '/t/' + code;
});
