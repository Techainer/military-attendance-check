import requests
import os
import time
import shutil

# Configuration
BASE_URL = "http://localhost:8000"
UPLOAD_ENDPOINT = f"{BASE_URL}/api/upload_chunk"
TEST_FILE_SIZE = 5 * 1024 * 1024  # 5MB
CHUNK_SIZE = 1 * 1024 * 1024      # 1MB
TEST_FILENAME = "test_large_video.mp4"

def create_test_file():
    print(f"Creating {TEST_FILENAME} ({TEST_FILE_SIZE} bytes)...")
    content = b"0" * TEST_FILE_SIZE
    with open(TEST_FILENAME, "wb") as f:
        f.write(content)
    return content

def test_chunked_upload():
    # Ensure server is up
    for _ in range(5):
        try:
            requests.get(BASE_URL)
            break
        except requests.exceptions.ConnectionError:
            print("Waiting for server...")
            time.sleep(1)
    else:
        print("Server not accessible")
        return False

    content = create_test_file()
    upload_id = f"test_{int(time.time())}"
    total_chunks = (TEST_FILE_SIZE + CHUNK_SIZE - 1) // CHUNK_SIZE
    
    print(f"Starting upload: {total_chunks} chunks")

    for i in range(total_chunks):
        start = i * CHUNK_SIZE
        end = min(start + CHUNK_SIZE, TEST_FILE_SIZE)
        chunk = content[start:end]
        
        files = {
            "file": (TEST_FILENAME, chunk)
        }
        data = {
            "chunk_index": str(i),
            "total_chunks": str(total_chunks),
            "upload_id": upload_id,
            "filename": TEST_FILENAME
        }
        
        print(f"Uploading chunk {i+1}/{total_chunks}...")
        response = requests.post(UPLOAD_ENDPOINT, files=files, data=data)
        
        if response.status_code != 200:
            print(f"Failed to upload chunk {i}: {response.text}")
            return False
        
        resp_json = response.json()
        if i == total_chunks - 1:
            if resp_json.get("status") == "success":
                print("Upload completed successfully!")
                return True
            else:
                print(f"Final chunk response invalid: {resp_json}")
                return False
        else:
            if resp_json.get("status") != "partial":
                print(f"Chunk response invalid: {resp_json}")
                return False

    return False

if __name__ == "__main__":
    try:
        if test_chunked_upload():
            print("TEST PASSED")
            # Cleanup
            if os.path.exists(TEST_FILENAME):
                os.remove(TEST_FILENAME)
        else:
            print("TEST FAILED")
            exit(1)
    except Exception as e:
        print(f"TEST ERROR: {e}")
        exit(1)
