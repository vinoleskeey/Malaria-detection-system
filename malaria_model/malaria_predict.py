import sys
import os
from typing import Tuple

import numpy as np

# Global variable to store the loaded model
_model = None
_last_model_path = None


def _get_model(model_path: str = 'malaria_cnn_model.keras'):
    """
    Load and return the model. Forces reload for each prediction to avoid caching.

    Args:
        model_path: Path to the Keras model file

    Returns:
        Loaded Keras model
    """
    from tensorflow.keras.models import load_model  # type: ignore[reportMissingModuleSource]  # deferred import - avoids loading TensorFlow at Flask startup

    global _model, _last_model_path
    # Always reload the model to ensure fresh predictions - no caching
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")
    # Always reload regardless of whether model was previously loaded
    # This ensures each prediction uses fresh model weights
    _model = load_model(model_path)
    _last_model_path = model_path
    return _model


def predict_malaria(img_path: str, model_path: str = 'malaria_cnn_model.keras') -> Tuple[str, float, float, float]:
    """
    Predict malaria infection from a cell image.

    Args:
        img_path: Path to the image file
        model_path: Path to the Keras model file (default: 'malaria_cnn_model.keras')

    Returns:
        Tuple containing (label, probability, malaria_score, no_malaria_score)
        - label: "Malaria Detected" or "No Malaria"
        - probability: Confidence percentage of the predicted class (0-100)
        - malaria_score: Probability of Malaria Detected (0-100)
        - no_malaria_score: Probability of No Malaria (0-100)

    Raises:
        FileNotFoundError: If image or model file not found
        ValueError: If image cannot be processed
    """
    from tensorflow.keras.preprocessing import image  # type: ignore[reportMissingModuleSource]  # deferred import - avoids loading TensorFlow at Flask startup

    # Validate image path
    if not os.path.exists(img_path):
        raise FileNotFoundError(f"Image file not found: {img_path}")

    # Load model (lazy loading)
    try:
        model = _get_model(model_path)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Model file not found: {model_path}. Please ensure the model file exists.") from e

    # Load and preprocess image
    # IMPORTANT: The model includes a Rescaling(1./255) layer, so we pass raw 0-255 values
    # DO NOT divide by 255 here - that would cause double normalization!
    try:
        img = image.load_img(img_path, target_size=(128, 128))
        img_array = image.img_to_array(img)
        img_array = np.expand_dims(img_array, axis=0)
        # Don't divide by 255 - model does this internally via Rescaling layer
    except Exception as e:
        raise ValueError(f"Failed to load or preprocess image: {e}") from e

    # Predict
    try:
        pred = model.predict(img_array, verbose=0)[0][0]
    except Exception as e:
        raise ValueError(f"Failed to make prediction: {e}") from e

    # Calculate scores for both classes
    # pred is the probability of class 1 (Malaria), so:
    # - malaria_score = pred * 100 (probability of Malaria Detected)
    # - no_malaria_score = (1 - pred) * 100 (probability of No Malaria)
    malaria_score = float(pred * 100)
    no_malaria_score = float((1 - pred) * 100)

    # Determine label and prediction confidence
    if pred > 0.5:
        label = "Malaria Detected"
        prob = malaria_score
    else:
        label = "No Malaria"
        prob = no_malaria_score

    return label, prob, malaria_score, no_malaria_score


### ---------------- CLI support ----------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python malaria_predict.py <image_path> [model_path]")
        print("  image_path: Path to the cell image to analyze")
        print("  model_path: (Optional) Path to the model file, default: malaria_cnn_model.keras")
        sys.exit(1)

    img_path = sys.argv[1]
    model_path = sys.argv[2] if len(sys.argv) > 2 else 'malaria_cnn_model.keras'

    try:
        result, probability, malaria_score, no_malaria_score = predict_malaria(img_path, model_path)
        # Output in a consistent format: label|probability|malaria_score|no_malaria_score
        print(f"{result}|{probability:.2f}|{malaria_score:.2f}|{no_malaria_score:.2f}")
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)