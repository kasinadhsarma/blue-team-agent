import gymnasium as gym
import numpy as np
import tensorflow as tf
from tensorflow.keras import layers
from flask import Flask, request, jsonify
import logging
import threading
from data_fetcher import DataFetcher
import json

# Initialize the iocs_appended flag
iocs_appended = False

# Configure logging
logging.basicConfig(filename='rl_agent_errors.log', level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Define the state representation and action space for cybersecurity data
num_inputs = 51  # Number of relevant features plus IOCs
num_actions = 10  # Number of possible actions

# List of relevant features for state representation
relevant_features = [
    'Flow Duration', 'Tot Fwd Pkts', 'Tot Bwd Pkts',
    'TotLen Fwd Pkts', 'TotLen Bwd Pkts',
    'Fwd Pkt Len Max', 'Fwd Pkt Len Min', 'Fwd Pkt Len Mean',
    'Fwd Pkt Len Std', 'Bwd Pkt Len Max', 'Bwd Pkt Len Min',
    'Bwd Pkt Len Mean', 'Bwd Pkt Len Std', 'Flow Byts/s',
    'Flow Pkts/s', 'Fwd IAT Tot', 'Bwd IAT Tot',
    'Fwd Header Len', 'Bwd Header Len', 'Fwd Pkts/s', 'Bwd Pkts/s',
    'Pkt Len Min', 'Pkt Len Max', 'Pkt Len Mean', 'Pkt Len Std',
    'Pkt Len Var', 'FIN Flag Cnt', 'SYN Flag Cnt', 'RST Flag Cnt',
    'PSH Flag Cnt', 'ACK Flag Cnt', 'URG Flag Cnt', 'CWE Flag Count',
    'ECE Flag Cnt', 'Pkt Size Avg', 'Fwd Seg Size Avg', 'Bwd Seg Size Avg',
    'Fwd Byts/b Avg', 'Fwd Pkts/b Avg', 'Fwd Blk Rate Avg', 'Bwd Byts/b Avg',
    'Bwd Pkts/b Avg', 'Bwd Blk Rate Avg', 'Subflow Fwd Pkts', 'Subflow Fwd Byts',
    'Subflow Bwd Pkts', 'Subflow Bwd Byts', 'Init Fwd Win Byts', 'Init Bwd Win Byts',
    'Fwd Act Data Pkts', 'Fwd Seg Size Min'
]

# Set random seeds for reproducibility
np.random.seed(42)
tf.random.set_seed(42)

# Define the neural network model
model = tf.keras.Sequential([
    layers.Input(shape=(num_inputs,)),
    layers.Dense(128, activation='relu'),
    layers.Dense(64, activation='relu'),
    layers.Dense(32, activation='relu'),
    layers.Dense(num_actions, activation='linear')
])

# Define the optimizer and loss function
optimizer = tf.keras.optimizers.Adam(learning_rate=0.001)
loss_function = tf.keras.losses.MeanSquaredError()

# Compile the model
model.compile(optimizer=optimizer, loss=loss_function)

# Define the training parameters
gamma = 0.99  # Discount factor for future rewards
epsilon = 1.0  # Exploration rate
epsilon_min = 0.01
epsilon_decay = 0.995
batch_size = 32
memory = []

# Function to choose an action based on the current state
def choose_action(state):
    if np.random.rand() <= epsilon:
        return np.random.randint(num_actions)
    q_values = model.predict(state)
    return np.argmax(q_values[0])

# Function to train the model
def train_model():
    if len(memory) < batch_size:
        return
    batch = np.random.choice(len(memory), batch_size, replace=False)
    for i in batch:
        state, action, reward, next_state, done = memory[i]
        target = reward
        if not done:
            next_state = np.reshape(next_state, [1, num_inputs])
            target += gamma * np.amax(model.predict(next_state)[0])
        state = np.reshape(state, [1, num_inputs])
        target_f = model.predict(state)
        target_f[0][action] = target
        model.fit(state, target_f, epochs=1, verbose=0)

def fetch_and_update_cve_data():
    source_url = "https://cve.circl.lu/api/last/10"  # Fetch the latest 10 CVEs
    output_file = "preprocessed_data.json"
    data_fetcher = DataFetcher(source_url, output_file)
    try:
        data_fetcher.run()
        with open(output_file, 'r') as f:
            cve_data = json.load(f)
        return cve_data
    except Exception as e:
        logging.error(f"Error fetching CVE data: {e}", exc_info=True)
        return []

def run_training_loop():
    global epsilon, memory, model
    num_episodes = 1000
    save_interval = 100  # Save the model every 100 episodes

    # Load preprocessed data from .npy files
    features = np.load('/home/ubuntu/home/ubuntu/blue-team-agent-fresh/src/preprocessed_otx_data.npy')
    labels = np.load('/home/ubuntu/home/ubuntu/blue-team-agent-fresh/src/preprocessed_otx_data_labels.npy')

    for episode in range(num_episodes):
        # Fetch and update CVE data at the beginning of each episode
        cve_data = fetch_and_update_cve_data()

        for i in range(len(features)):
            state = features[i]
            state = np.reshape(state, [1, num_inputs])
            action = choose_action(state)
            next_state = features[(i + 1) % len(features)]
            next_state = np.reshape(next_state, [1, num_inputs])
            reward = labels[i]
            done = (i == len(features) - 1)
            memory.append((state, action, reward, next_state, done))
            train_model()
        if epsilon > epsilon_min:
            epsilon *= epsilon_decay
        if (episode + 1) % save_interval == 0:
            model.save(f'rl_agent_model_{episode + 1}.h5')
    # Save the trained model
    model.save('rl_agent_model.h5')

# Flask web server to receive log data from Logstash
app = Flask(__name__)

@app.route('/logs', methods=['POST'])
def receive_logs():
    try:
        log_data = request.json
        print(f"Received log data: {log_data}")
        logging.info(f"Received log data: {log_data}")

        # Convert log data to state representation for the RL agent
        state = convert_log_to_state(log_data, cve_data)
        print(f"Converted state: {state}")
        logging.info(f"Converted state: {state}")

        # Choose an action based on the current state
        action = choose_action(state)
        print(f"Chosen action: {action}")
        logging.info(f"Chosen action: {action}")

        # Extract parameters from log data
        ip_address = log_data.get('ip_address')
        rate_limit = log_data.get('rate_limit')
        system_id = log_data.get('system_id')
        message = log_data.get('message')
        settings = log_data.get('settings')
        query = log_data.get('query')

        # Execute the chosen action with dynamic parameters
        execute_action(action, ip_address=ip_address, rate_limit=rate_limit, system_id=system_id, message=message, settings=settings, query=query)
        logging.info(f"Executed action: {action}")

        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Error processing log data: {e}")
        logging.error("Error processing log data", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

def convert_log_to_state(log_data, cve_data):
    import os
    global iocs_appended
    # Reset the iocs_appended flag to False at the beginning of the function
    iocs_appended = False
    # Load IOCs from JSON file
    script_dir = os.path.dirname(__file__)
    iocs_path = os.path.join(script_dir, 'iocs.json')
    if not os.path.exists(iocs_path):
        iocs_path = 'src/iocs.json'  # Adjust path for CI/CD environment
    with open(iocs_path, 'r') as f:
        iocs = json.load(f)

    # Extract relevant metrics from CVE data
    cve_count = len(cve_data)
    cve_severity = sum(item.get('cvss', {}).get('score', 0) for item in cve_data) / cve_count if cve_count > 0 else 0

    # Create state representation
    state = []
    for feature in relevant_features:
        value = log_data.get(feature, 0)
        state.append(value)
    print(f"State array after appending relevant features: {state}")
    print(f"Length after appending relevant features: {len(state)}")

    # Add binary features for IOCs
    url_ioc = 1 if any(url in log_data.get('message', '') for url in iocs['urls']) else 0
    fqdn_ioc = 1 if any(fqdn in log_data.get('message', '') for fqdn in iocs['fqdns']) else 0
    ipv4_ioc = 1 if any(ipv4 in log_data.get('message', '') for ipv4 in iocs['ipv4s']) else 0

    # Ensure binary IOC features are only appended once
    print(f"State array before appending IOCs: {state}")
    if len(state) == len(relevant_features) - 3 and not iocs_appended:
        state.extend([url_ioc, fqdn_ioc, ipv4_ioc])
        iocs_appended = True
    print(f"State array after appending IOCs: {state}")
    print(f"Length after appending IOCs: {len(state)}")

    # Ensure the state array has the correct length
    expected_length = 51  # Update to match the number of metrics plus IOCs
    if len(state) != expected_length:
        print(f"State array: {state}")
        raise ValueError(f"State array length is {len(state)}, expected {expected_length}")

    # Adjust the state array length if necessary
    print(f"State array before truncating: {state}")
    state = state[:expected_length]
    print(f"State array after truncating: {state}")
    state = np.array(state)
    state = np.reshape(state, [1, expected_length])  # Update to match the number of metrics plus IOCs
    print(f"Final state array: {state}")
    return state

def execute_action(action, ip_address=None, rate_limit=None, system_id=None, message=None, settings=None, query=None):
    # Define actions based on the action space
    actions = [
        "block_ip",
        "allow_ip",
        "rate_limit",
        "isolate_system",
        "notify_admin",
        "run_malware_scan",
        "change_firewall_settings",
        "update_software",
        "search_logs",
        "generate_report"
    ]

    # Execute the chosen action
    chosen_action = actions[action]
    print(f"Executing action: {chosen_action}")

    # Implement the logic to interact with the blue team's infrastructure based on the chosen action
    if chosen_action == "block_ip":
        block_ip_address(ip_address)
    elif chosen_action == "allow_ip":
        allow_ip_address(ip_address)
    elif chosen_action == "rate_limit":
        apply_rate_limiting(ip_address, rate_limit)
    elif chosen_action == "isolate_system":
        isolate_compromised_system(system_id)
    elif chosen_action == "notify_admin":
        send_alert_to_admin(message)
    elif chosen_action == "run_malware_scan":
        trigger_malware_scan(system_id)
    elif chosen_action == "change_firewall_settings":
        update_firewall_settings(settings)
    elif chosen_action == "update_software":
        update_software_packages(system_id)
    elif chosen_action == "search_logs":
        perform_log_search(query)
    elif chosen_action == "generate_report":
        generate_security_report()

def block_ip_address(ip_address):
    import requests
    firewall_api_url = "http://localhost:5001/api/block_ip"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "ip_address": ip_address
    }
    try:
        response = requests.post(firewall_api_url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Successfully blocked IP address: {ip_address}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to block IP address: {ip_address}. Error: {e}")

def allow_ip_address(ip_address):
    import requests
    firewall_api_url = "http://localhost:5001/api/allow_ip"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "ip_address": ip_address
    }
    try:
        response = requests.post(firewall_api_url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Successfully allowed IP address: {ip_address}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to allow IP address: {ip_address}. Error: {e}")

def apply_rate_limiting(ip_address, rate_limit):
    import requests
    rate_limit_api_url = "http://localhost:5001/api/rate_limit"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "ip_address": ip_address,
        "rate_limit": rate_limit
    }
    try:
        response = requests.post(rate_limit_api_url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Successfully applied rate limiting to IP address: {ip_address}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to apply rate limiting to IP address: {ip_address}. Error: {e}")

def isolate_compromised_system(system_id):
    import requests
    isolate_api_url = "http://localhost:5001/api/isolate_system"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "system_id": system_id
    }
    try:
        response = requests.post(isolate_api_url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Successfully isolated system: {system_id}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to isolate system: {system_id}. Error: {e}")

def send_alert_to_admin(message):
    import requests
    alert_api_url = "http://localhost:5001/api/send_alert"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "message": message
    }
    try:
        response = requests.post(alert_api_url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Successfully sent alert: {message}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to send alert: {message}. Error: {e}")

def trigger_malware_scan(system_id):
    import requests
    malware_scan_api_url = "http://localhost:5001/api/trigger_malware_scan"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "system_id": system_id
    }
    try:
        response = requests.post(malware_scan_api_url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Successfully triggered malware scan on system: {system_id}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to trigger malware scan on system: {system_id}. Error: {e}")

def update_firewall_settings(settings):
    import requests
    firewall_settings_api_url = "http://localhost:5001/api/update_settings"
    headers = {
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(firewall_settings_api_url, headers=headers, json=settings)
        response.raise_for_status()
        print("Successfully updated firewall settings")
    except requests.exceptions.RequestException as e:
        print(f"Failed to update firewall settings. Error: {e}")

def update_software_packages(system_id):
    import requests
    update_packages_api_url = "http://localhost:5001/api/update_packages"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "system_id": system_id
    }
    try:
        response = requests.post(update_packages_api_url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Successfully updated software packages on system: {system_id}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to update software packages on system: {system_id}. Error: {e}")

def perform_log_search(query):
    import requests
    log_search_api_url = "http://localhost:5001/api/search_logs"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "query": query
    }
    try:
        response = requests.post(log_search_api_url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Successfully performed log search with query: {query}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to perform log search with query: {query}. Error: {e}")

def generate_security_report():
    import requests
    report_api_url = "http://localhost:5001/api/generate_report"
    headers = {
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(report_api_url, headers=headers)
        response.raise_for_status()
        print("Successfully generated security report")
    except requests.exceptions.RequestException as e:
        print(f"Failed to generate security report. Error: {e}")

if __name__ == '__main__':
    print("Starting Flask server...")
    flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000))
    flask_thread.start()
    run_training_loop()
