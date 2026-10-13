# fixture in Znuny's documented format, not copied from a Znuny install
[
    {
        Key        => 'Vendor',
        Name       => 'Vendor',
        Searchable => 1,
        Input      => {
            Type      => 'Text',
            Size      => 50,
            MaxLength => 50,
        },
    },
    {
        Key   => 'Type',
        Name  => 'Type',
        Input => {
            Type         => 'GeneralCatalog',
            Class        => 'ITSM::ConfigItem::Computer::Type',
            Translation  => 1,
            Required     => 1,
        },
    },
    {
        Key      => 'CPU',
        Name     => 'CPU',
        Input    => { Type => 'Text', Size => 50, MaxLength => 100 },
        CountMin => 0,
        CountMax => 16,
        CountDefault => 1,
    },
    {
        Key      => 'NIC',
        Name     => 'Network Adapter',
        Input    => { Type => 'Text', Size => 50, MaxLength => 100 },
        CountMin => 0,
        CountMax => 10,
        Sub => [
            {
                Key        => 'IPAddress',
                Name       => 'IP Address',
                Searchable => 1,
                Input      => { Type => 'Text', Size => 40, MaxLength => 40 },
                CountMin   => 0,
                CountMax   => 20,
            },
        ],
    },
    {
        Key   => 'Location',
        Name  => 'Location',
        Input => { Type => 'CI', CIClassName => 'Location' },
    },
]
