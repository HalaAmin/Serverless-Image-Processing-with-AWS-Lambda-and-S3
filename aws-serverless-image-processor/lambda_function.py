import boto3
import os
from urllib.parse import unquote_plus
from PIL import Image
from uuid import uuid4
from datetime import datetime
import json

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
dynamo_table = dynamodb.Table('hala-db') # Table name

# Extract metadata from image file
def get_image_metadata(image_path):
  with Image.open(image_path) as img:
      return {
          'width': img.width,
          'height': img.height,
          'format': img.format,
          'mode': img.mode,
          'size_bytes': os.path.getsize(image_path)
      }

# Resize image to 50% of original size
def resize_image(image_path, resized_path):
  with Image.open(image_path) as image:
      original_size = image.size    # Capture original dimensions
      image.thumbnail(tuple(x / 2 for x in image.size))
      resized_size = image.size     # Capture resized dimensions
      image.save(resized_path)
      return original_size, resized_size  
  
def lambda_handler(event, context):
  for record in event['Records']:
      try:
          # Extract S3 event details
          bucket_name = record['s3']['bucket']['name']
          object_key = unquote_plus(record['s3']['object']['key'])
          size = record['s3']['object'].get('size', -1)
          event_name = record['eventName']
          event_time = record['eventTime']
          
          # Generate unique file names
          base_name = os.path.basename(object_key)
          download_path = f'/tmp/{uuid4()}_{base_name}'
          upload_path = f'/tmp/resized_{base_name}'
          resized_object_key = f'resized-{base_name}'
          
          # Download original image
          s3_client.download_file(bucket_name, object_key, download_path)

          # Get original image metadata
          original_metadata = get_image_metadata(download_path)

          # Resize image and get dimensions
          original_size, resized_size = resize_image(download_path, upload_path)

          # Get resized image metadata
          resized_metadata = get_image_metadata(upload_path)

          # Upload resized image
          s3_client.upload_file(
              upload_path, 
              'dest-bucket-image-out', 
              resized_object_key,
              ExtraArgs={
                    'Metadata': {
                        'original_filename': base_name,
                        'original_bucket': bucket_name,
                        'resized_dimensions': f'{resized_size[0]}x{resized_size[1]}',
                        'processing_time': datetime.utcnow().isoformat()
                    }
                }
          )
          
          # Store comprehensive metadata in DynamoDB
          dynamo_table.put_item(
              Item={
                    # Partition key = resource-id (String) from "General information"
                    'resource-id': str(uuid4()),
                    'EventTime': event_time,
                    'EventType': event_name,
                    
                    # Original image info from source S3 (src-bucket-image-in)
                    'OriginalBucket': bucket_name,
                    'OriginalObjectKey': object_key,
                    'OriginalSize': size,
                    'OriginalWidth': int(original_metadata['width']),
                    'OriginalHeight': int(original_metadata['height']),
                    'OriginalFormat': original_metadata['format'],
                    'OriginalMode': original_metadata['mode'],
                    'OriginalFileSize': int(original_metadata['size_bytes']),
                    
                    # Resized image info from destination S3 (dest-bucket-image-out)
                    'ResizedBucket': 'dest-bucket-image-out',
                    'ResizedObjectKey': resized_object_key,
                    'ResizedWidth': int(resized_metadata['width']),
                    'ResizedHeight': int(resized_metadata['height']),
                    'ResizedFormat': resized_metadata['format'],
                    'ResizedMode': resized_metadata['mode'],
                    'ResizedFileSize': int(resized_metadata['size_bytes']),

                    # Processing info
                    'ProcessingTime': datetime.utcnow().isoformat(),
                    'ReductionPercentage': int(round((1 - (resized_metadata['size_bytes'] / original_metadata['size_bytes'])) * 100, 2)),
                    'DimensionReduction': f"{original_size[0]}x{original_size[1]} → {resized_size[0]}x{resized_size[1]}",
                    
                    # S3 event context
                    'EventSource': 'aws:s3',
                    'AWSRegion': record.get('awsRegion', ''),
                    'EventVersion': record.get('eventVersion', '')
                }
           )
          
          # Print log success message
          print(f"Successfully processed {object_key}. Original: {original_metadata['size_bytes']} bytes, Resized: {resized_metadata['size_bytes']} bytes")

          # Clean up temporary files
          try:
              os.remove(download_path)
              os.remove(upload_path)
          except Exception as cleanup_error:
              print(f"Warning: Could not clean up temp files: {cleanup_error}")

      except Exception as e:
          print(f"Error processing {object_key}: {str(e)}")
          raise e
  
  return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Image processing completed successfully',
            'processed_count': len(event['Records'])
        })
    }