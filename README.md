# Lambda CSV Bronze Uploader

This folder contains a CloudFormation template and a Python client for uploading a local file to an existing S3 Bronze bucket. The Lambda returns a short-lived, checksum-bound presigned URL; the client streams the file directly to S3. The file contents do not pass through Lambda.

The CSV dataset is intentionally excluded from Git. Keep the source file locally and upload it with the client.

## Prerequisites

- Python 3.10 or newer
- AWS CLI configured with credentials for the target account
- `boto3` installed: `python -m pip install boto3`
- An existing bucket named `data-bronze-<account-id>-<region>-an`
- The Lambda execution role `LabRole` must already allow `s3:PutObject` on the chosen Bronze prefix
- The caller identity must be allowed to invoke the IAM-authenticated Lambda Function URL

## Deploy

Deploy the template in the same AWS account and region as the Bronze bucket. The default prefix is `raw/`.

```powershell
aws cloudformation deploy `
  --template-file .\lambda-bucket.yml `
  --stack-name bronze-csv-upload `
  --parameter-overrides BronzePrefix=raw/ `
  --region us-east-1
```

Retrieve the Function URL from the stack output:

```powershell
$Endpoint = aws cloudformation describe-stacks `
  --stack-name bronze-csv-upload `
  --query "Stacks[0].Outputs[?OutputKey=='UploadApiEndpoint'].OutputValue | [0]" `
  --output text `
  --region us-east-1
```

## Upload

Pass the endpoint and local file to the uploader. Add `--profile <profile>` when using a named AWS CLI profile.

```powershell
python .\upload_csv.py `
  --endpoint $Endpoint `
  --file .\operacoes.indiretas.automaticas.csv `
  --profile default
```

The client computes the SHA-256 checksum in chunks, requests a presigned URL using AWS Signature Version 4, streams the file to S3, and reports the object key and checksum. Files up to S3's single `PutObject` size limit are supported.
