import oci
import logging
import time
import sys
import telebot
import datetime
from dotenv import load_dotenv
import os

# ============================ CONFIGURATION ============================ #

load_dotenv()

availabilityDomains = os.getenv("AVAILABILITY_DOMAINS").split(",")
displayName = os.getenv("DISPLAY_NAME")
compartmentId = os.getenv("COMPARTMENT_ID")
subnetId = os.getenv("SUBNET_ID")
ssh_authorized_keys = os.getenv("SSH_AUTHORIZED_KEYS")

imageId = os.getenv("IMAGE_ID")
boot_volume_size_in_gbs = os.getenv("BOOT_VOLUME_SIZE_IN_GBS")
boot_volume_id = os.getenv("BOOT_VOLUME_ID")

bot_token = os.getenv("BOT_TOKEN")
uid = os.getenv("UID")

ocpus = int(os.getenv("OCPUS") or 4)
memory_in_gbs = int(os.getenv("MEMORY_IN_GBS") or 24)
minimum_time_interval = int(os.getenv("MINIMUM_TIME_INTERVAL") or 1)
region = os.getenv("OCI_REGION") # Добавили чтение региона

# ============================ LOGGING SETUP ============================ #

LOG_FORMAT = "[%(levelname)s] %(asctime)s - %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[logging.StreamHandler(sys.stdout)]
)

logging.info("#####################################################")
logging.info("Script to spawn VM.Standard.A1.Flex instance")

# ============================ INITIAL SETUP ============================ #

# ============================ INITIAL SETUP ============================ #

if bot_token and bot_token != "xxxx":
    bot = telebot.TeleBot(bot_token)
    
message = f"Start spawning instance VM.Standard.A1.Flex - {ocpus} ocpus - {memory_in_gbs} GB"
logging.info(message)

logging.info("Loading OCI config")
# Загружаем конфиг, но сразу форсируем регион из ENV
config = oci.config.from_file(file_location="./config")

# Берем регион из GitHub Action (env) или оставляем из файла
env_region = os.getenv("OCI_REGION")
if env_region:
    config['region'] = env_region
    logging.info(f"Region forced from environment: {config['region']}")

logging.info("Initialize OCI service clients")
# Явно передаем регион в каждый клиент на случай, если конфиг его теряет
compute_client = oci.core.ComputeClient(config, region=config['region'])
identity_client = oci.identity.IdentityClient(config, region=config['region'])
vcn_client = oci.core.VirtualNetworkClient(config, region=config['region'])
volume_client = oci.core.BlockstorageClient(config, region=config['region'])

# Проверка связи
try:
    tenancy = identity_client.get_tenancy(tenancy_id=config['tenancy']).data
    cloud_name = tenancy.name
    email = identity_client.list_users(compartment_id=compartmentId).data[0].email
    logging.info(f"Successfully connected to {cloud_name} ({email})")
except Exception as e:
    logging.error(f"Failed to connect to Oracle Cloud: {e}")
    sys.exit(1)
# ============================ STORAGE CHECK ============================ #

logging.info("Checking available storage in account")
total_volume_size = 0

if imageId != "xxxx":
    try:
        list_volumes = volume_client.list_volumes(compartment_id=compartmentId).data
    except Exception as e:
        logging.error(f"{e.status} - {e.code} - {e.message}")
        logging.error("Error detected. Check config and credentials. **SCRIPT STOPPED**")
        sys.exit()

    # Sum all active block and boot volumes
    for volume in list_volumes:
        if volume.lifecycle_state not in ("TERMINATING", "TERMINATED"):
            total_volume_size += volume.size_in_gbs

    for ad in availabilityDomains:
        boot_volumes = volume_client.list_boot_volumes(
            availability_domain=ad, compartment_id=compartmentId
        ).data
        for bvol in boot_volumes:
            if bvol.lifecycle_state not in ("TERMINATING", "TERMINATED"):
                total_volume_size += bvol.size_in_gbs

    free_storage = 200 - total_volume_size
    required_storage = (
        47 if boot_volume_size_in_gbs == "xxxx" else int(boot_volume_size_in_gbs)
    )

    if free_storage < required_storage:
        logging.critical(
            f"Only {free_storage} GB free out of 200 GB. "
            f"{required_storage} GB needed. **SCRIPT STOPPED**"
        )
        sys.exit()

# ============================ INSTANCE CHECK ============================ #

logging.info("Checking current instances")
instances = compute_client.list_instances(compartment_id=compartmentId).data

total_ocpus = total_memory = active_A1_instances = 0
instance_names = []

if instances:
    logging.info(f"{len(instances)} instance(s) found!")
    for instance in instances:
        logging.info(
            f"{instance.display_name} - {instance.shape} - "
            f"{int(instance.shape_config.ocpus)} ocpus - "
            f"{instance.shape_config.memory_in_gbs} GB | State: {instance.lifecycle_state}"
        )
        instance_names.append(instance.display_name)
        if instance.shape == "VM.Standard.A1.Flex" and instance.lifecycle_state not in ("TERMINATING", "TERMINATED"):
            active_A1_instances += 1
            total_ocpus += int(instance.shape_config.ocpus)
            total_memory += int(instance.shape_config.memory_in_gbs)
else:
    logging.info("No instances found!")

logging.info(
    f"Total ocpus: {total_ocpus} | Total memory: {total_memory} GB || "
    f"Free: {4 - total_ocpus} ocpus, {24 - total_memory} GB memory"
)

# Free-tier resource check
if total_ocpus + ocpus > 4 or total_memory + memory_in_gbs > 24:
    logging.critical("Free-tier resource limit exceeded (4 OCPUs / 24 GB max). **SCRIPT STOPPED**")
    sys.exit()

if displayName in instance_names:
    logging.critical(f"Duplicate display name '{displayName}' detected. **SCRIPT STOPPED**")
    sys.exit()

logging.info(f"Precheck passed! Ready to create instance: {ocpus} ocpus, {memory_in_gbs} GB")

# ============================ INSTANCE LAUNCH ============================ #

# ============================ INSTANCE LAUNCH ============================ #

# Определяем источник (Image или Boot Volume)
if imageId and imageId != "xxxx":
    logging.info(f"Using Image ID: {imageId}")
    # Формируем детали образа
    if boot_volume_size_in_gbs and boot_volume_size_in_gbs != "xxxx":
        source_details = oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=imageId,
            boot_volume_size_in_gbs=int(boot_volume_size_in_gbs)
        )
    else:
        source_details = oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=imageId
        )
elif boot_volume_id and boot_volume_id != "xxxx":
    logging.info(f"Using Boot Volume ID: {boot_volume_id}")
    source_details = oci.core.models.InstanceSourceViaBootVolumeDetails(
        source_type="bootVolume", 
        boot_volume_id=boot_volume_id
    )
else:
    logging.critical("No valid imageId or bootVolumeId found. **SCRIPT STOPPED**")
    sys.exit()


# ============================ RETRY LOOP ============================ #

wait_s_for_retry = 1
total_count = j_count = tc = oc = 0

while True:
    for ad in availabilityDomains:
        instance_detail = oci.core.models.LaunchInstanceDetails(
            metadata={"ssh_authorized_keys": ssh_authorized_keys},
            availability_domain=ad,
            shape="VM.Standard.A1.Flex",
            compartment_id=compartmentId,
            display_name=displayName,
            is_pv_encryption_in_transit_enabled=True,
            source_details=source_details,
            create_vnic_details=oci.core.models.CreateVnicDetails(
                assign_public_ip=True, subnet_id=subnetId
            ),
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=ocpus, memory_in_gbs=memory_in_gbs
            )
        )

        try:
            # Attempt to create instance
            launch_resp = compute_client.launch_instance(instance_detail)
            time.sleep(60)

            # Get public IP
            vnic_attachments = compute_client.list_vnic_attachments(
                compartment_id=compartmentId, instance_id=launch_resp.data.id
            ).data
            private_ips = vcn_client.list_private_ips(
                subnet_id=subnetId, vnic_id=vnic_attachments[0].vnic_id
            ).data
            public_ip = vcn_client.get_public_ip_by_private_ip_id(
                oci.core.models.GetPublicIpByPrivateIpIdDetails(private_ip_id=private_ips[0].id)
            ).data.ip_address

            total_count += 1
            logging.info(
                f'"{displayName}" VPS created successfully! IP: {public_ip}, '
                f"Retries: {total_count}, Cloud: {cloud_name}, Email: {email}"
            )

            # Telegram success message
            if bot_token != "xxxx" and uid != "xxxx" and msg_id:
                while True:
                    try:
                        bot.delete_message(uid, msg_id)
                        bot.send_message(
                            uid,
                            f'"{displayName}" VPS created successfully!\n'
                            f"Cloud Account: {cloud_name}\n"
                            f"Email: {email}\n"
                            f"Number of Retry: {total_count}\n"
                            f"VPS IP: {public_ip}"
                        )
                        break
                    except Exception:
                        time.sleep(5)

            sys.exit()

        except oci.exceptions.ServiceError as e:
            total_count += 1
            j_count += 1

            if j_count == 10:
                j_count = 0
                if bot_token != "xxxx" and uid != "xxxx" and msg_id:
                    try:
                        msg = (
                            f"Cloud Account: {cloud_name}\n"
                            f"Email: {email}\n"
                            f"Number of Retry: {total_count}\n"
                            f"Bot Status: Running\n"
                            f"Last Checked (UTC): {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M:%S}"
                        )
                        bot.edit_message_text(msg, uid, msg_id)
                    except Exception:
                        pass

            # Handle throttling and other errors
            if e.status == 429:
                oc = 0
                tc += 1
                if tc == 2:
                    wait_s_for_retry += 1
                    tc = 0
            else:
                tc = 0
                if wait_s_for_retry > minimum_time_interval:
                    oc += 1
                if oc == 2:
                    wait_s_for_retry -= 1
                    oc = 0

            logging.info(
                f"{e.status} - {e.code} - {e.message}. Retrying after {wait_s_for_retry}s. "
                f"Retry count: {total_count}"
            )
            time.sleep(wait_s_for_retry)

        except Exception as e:
            total_count += 1
            j_count += 1
            if j_count == 10:
                j_count = 0
                if bot_token != "xxxx" and uid != "xxxx" and msg_id:
                    try:
                        msg = (
                            f"Cloud Account: {cloud_name}\n"
                            f"Email: {email}\n"
                            f"Number of Retry: {total_count}\n"
                            f"Bot Status: Running\n"
                            f"Last Checked (UTC): {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M:%S}"
                        )
                        bot.edit_message_text(msg, uid, msg_id)
                    except Exception:
                        pass

            logging.info(f"{e}. Retrying after {wait_s_for_retry}s. Retry count: {total_count}")
            time.sleep(wait_s_for_retry)

        except KeyboardInterrupt:
            sys.exit()
