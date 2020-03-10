package ovirtprovider

import (
	"context"
	"encoding/json"
	"fmt"

	ovirtsdk "github.com/ovirt/go-ovirt"
	kubevirtv1alpha1 "github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/apis/v2v/v1alpha1"
)

// Client struct holding implementation details required to interact with oVirt engine
type Client struct {
	conn    *ovirtsdk.Connection
	ctx     context.Context
	Cluster string
}

// NewClient creates new client struct based on connection details provided
func NewClient(ctx context.Context, url string, username string, password string, cluster string) (*Client, error) {
	conn, err := ovirtsdk.NewConnectionBuilder().
		URL(url).
		Username(username).
		Password(password).
		// TODO: check how can we provide CA in the UI
		Insecure(true).
		Build()
	if err != nil {
		return nil, err
	}

	c := &Client{
		conn:    conn,
		ctx:     ctx,
		Cluster: cluster,
	}
	return c, nil
}

// Close makes sure that connection is closed
func (c *Client) Close() {
	c.conn.Close()
}

// GetVMs returns a list of vms from oVirt
func (c *Client) GetVMs() ([]string, error) {
	vmsService := c.conn.SystemService().VmsService()
	vmsResponse, err := vmsService.List().Search(fmt.Sprintf("cluster=%v", c.Cluster)).Send()
	if err != nil {
		return nil, err
	}

	var vmNames []string
	if vms, ok := vmsResponse.Vms(); ok {
		for _, vm := range vms.Slice() {
			if vmName, ok := vm.Name(); ok {
				vmNames = append(vmNames, vmName)
			}
		}
	}
	return vmNames, nil
}

// GetVM returns a specifc vm identified by name
func (c *Client) GetVM(name string) (*kubevirtv1alpha1.OVirtVMDetail, error) {
	response, err := c.conn.SystemService().VmsService().List().Search(fmt.Sprintf("name=%s and cluster=%s", name, c.Cluster)).Send()
	if err != nil {
		return nil, err
	}
	vms, _ := response.Vms()
	if len(vms.Slice()) != 1 {
		return nil, fmt.Errorf("Virtual machine %s not found in cluster %s", name, c.Cluster)
	}
	raw, err := c.getRaw(vms.Slice()[0])
	if err != nil {
		return nil, err
	}
	vmDetail := &kubevirtv1alpha1.OVirtVMDetail{
		Raw: raw,
	}
	return vmDetail, nil
}

type vm struct {
	CPUCores int64  `json:"cores"`
	Disks    []disk `json:"disks"`
	ID       string `json:"id"`
	Memory   int64  `json:"memory"`
	Name     string `json:"name"`
	Nics     []nic  `json:"nics"`
	OsType   string `json:"ostype"`
}

type disk struct {
	Bootable          bool   `json:"bootable"`
	ID                string `json:"id"`
	Name              string `json:"name"`
	Size              int64  `json:"size"`
	StorageDomainName string `json:"sdname"`
	StorageDomainID   string `json:"sdid"`
}

type nic struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Mac  string `json:"mac"`
}

func (c *Client) getRaw(sourceVM *ovirtsdk.Vm) (string, error) {
	vm := &vm{}
	if vmName, ok := sourceVM.Name(); ok {
		vm.Name = vmName
	}
	if vmID, ok := sourceVM.Id(); ok {
		vm.ID = vmID
	}
	if memory, ok := sourceVM.Memory(); ok {
		vm.Memory = memory
	}
	if cpu, ok := sourceVM.Cpu(); ok {
		if topology, ok := cpu.Topology(); ok {
			if cores, ok := topology.Cores(); ok {
				vm.CPUCores = cores
			}

		}
	}
	if os, ok := sourceVM.Os(); ok {
		if osType, ok := os.Type(); ok {
			vm.OsType = osType
		}
	}
	diskAttachmentsLink, _ := sourceVM.DiskAttachments()
	diskAttachments, err := c.conn.FollowLink(diskAttachmentsLink)
	if err != nil {
		return "", err
	}
	for _, diskAttachment := range diskAttachments.(*ovirtsdk.DiskAttachmentSlice).Slice() {
		disk := &disk{}
		if id, ok := diskAttachment.Id(); ok {
			disk.ID = id
		}
		if name, ok := diskAttachment.Name(); ok {
			disk.Name = name
		}
		if bootable, ok := diskAttachment.Bootable(); ok {
			disk.Bootable = bootable
		}
		diskLink, _ := diskAttachment.Disk()
		vmDisk, err := c.conn.FollowLink(diskLink)
		if err != nil {
			return "", err
		}
		if size, ok := vmDisk.(*ovirtsdk.Disk).ProvisionedSize(); ok {
			disk.Size = size
		}
		sdLink, _ := vmDisk.(*ovirtsdk.Disk).StorageDomains()
		sd, err := c.conn.FollowLink(sdLink.Slice()[0])
		if err != nil {
			return "", err
		}
		if sdName, ok := sd.(*ovirtsdk.StorageDomain).Name(); ok {
			disk.StorageDomainName = sdName
		}
		if sdID, ok := sd.(*ovirtsdk.StorageDomain).Id(); ok {
			disk.StorageDomainID = sdID
		}
		vm.Disks = append(vm.Disks, *disk)
	}
	nicsLink, _ := sourceVM.Nics()
	nics, err := c.conn.FollowLink(nicsLink)
	if err != nil {
		return "", err
	}
	for _, vmNic := range nics.(*ovirtsdk.NicSlice).Slice() {
		nic := &nic{}
		if name, ok := vmNic.Name(); ok {
			nic.Name = name
		}
		if id, ok := vmNic.Id(); ok {
			nic.ID = id
		}
		if mac, ok := vmNic.Mac(); ok {
			if addr, ok := mac.Address(); ok {
				nic.Mac = addr
			}
		}
		vm.Nics = append(vm.Nics, *nic)
	}
	raw, err := json.Marshal(vm)
	return string(raw), err
}
