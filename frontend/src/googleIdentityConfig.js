export function buildGoogleIdentityConfig(clientId, callback) {
  return {
    client_id: clientId,
    callback,
    use_fedcm_for_button: true,
  };
}
