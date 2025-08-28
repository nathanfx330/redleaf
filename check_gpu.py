# --- File: check_gpu.py ---
import spacy

print("--- GPU Check for spaCy ---")
try:
    # The spacy.require_gpu() function is the key.
    # It will try to initialize the GPU and will raise an error if it can't.
    spacy.require_gpu()
    print("\nSUCCESS: GPU is available and spaCy can use it!")
    print("Your Redleaf application is correctly configured for GPU acceleration.")

except Exception as e:
    print("\nFAILURE: spaCy could not access the GPU.")
    print("This is why you are not seeing GPU engagement.")
    print("\n--- Error Details ---")
    print(e)
    print("\n--- Next Steps ---")
    print("This usually means there's an issue with the CUDA or CuPy installation.")
    print("Please ensure your NVIDIA drivers are up to date and re-check the installation steps.")

print("-------------------------")